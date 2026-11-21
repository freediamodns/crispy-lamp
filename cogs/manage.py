from __future__ import annotations

import asyncio
from typing import Literal

import discord
from discord.ext import commands

from cogs.stats import build_stats_view
from services import (
    can_manage,
    container_from_message,
    container_specs,
    docker_error_kwargs,
    docker_service,
    is_admin,
    management_text,
    owner_from_message,
    stats_values,
    status_name,
    store,
    utcnow,
)
from ui_components import text_view


class ManagementView(discord.ui.LayoutView):
    """Persistent controls. The panel text supplies its target after restarts."""

    def __init__(
        self,
        record: dict | None = None,
        container=None,
        live: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        details = (
            management_text(record, container, live)
            if record is not None and container is not None
            else "Open a management panel with `>manage` to load VPS details."
        )
        panel = discord.ui.Container(
            discord.ui.TextDisplay(f"# 🛠️ KTS Host VPS Manager\n\n{details}"),
            discord.ui.Separator(),
            accent_color=discord.Color.blurple(),
        )
        first_row = discord.ui.ActionRow(
            self._button("Start", "▶", discord.ButtonStyle.success, "start", "kts:vps:start"),
            self._button("Stop", "⏹", discord.ButtonStyle.secondary, "stop", "kts:vps:stop"),
            self._button("Restart", "🔄", discord.ButtonStyle.primary, "restart", "kts:vps:restart"),
            self._button("Live Stats", "📊", discord.ButtonStyle.primary, "stats", "kts:vps:stats"),
        )
        second_row = discord.ui.ActionRow(
            self._button("SSHX", "🔑", discord.ButtonStyle.primary, "sshx", "kts:vps:sshx"),
            self._button("Delete", "🗑", discord.ButtonStyle.danger, "delete", "kts:vps:delete"),
        )
        select = discord.ui.Select(
            placeholder="Choose a management action…",
            options=[
                discord.SelectOption(label="Start", value="start", emoji="▶"),
                discord.SelectOption(label="Stop", value="stop", emoji="⏹"),
                discord.SelectOption(label="Restart", value="restart", emoji="🔄"),
                discord.SelectOption(label="Live Stats", value="stats", emoji="📊"),
                discord.SelectOption(label="SSHX", value="sshx", emoji="🔑"),
                discord.SelectOption(label="Delete (admin)", value="delete", emoji="🗑"),
            ],
            custom_id="kts:vps:select",
        )
        select.callback = self._select_action
        panel.add_item(first_row)
        panel.add_item(second_row)
        panel.add_item(discord.ui.ActionRow(select))
        self.add_item(panel)

    def _button(
        self,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        action: Literal["start", "stop", "restart", "stats", "sshx", "delete"],
        custom_id: str,
    ) -> discord.ui.Button:
        button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=custom_id)

        async def callback(interaction: discord.Interaction) -> None:
            await self.perform(interaction, action)

        button.callback = callback
        return button

    async def _select_action(self, interaction: discord.Interaction) -> None:
        await self.perform(interaction, interaction.data["values"][0])

    async def _owner_id(self, interaction: discord.Interaction) -> int | None:
        if not interaction.message:
            return None
        return owner_from_message(interaction.message)

    async def _container_id(self, interaction: discord.Interaction) -> str | None:
        if not interaction.message:
            return None
        return container_from_message(interaction.message)

    async def _authorized(self, interaction: discord.Interaction, owner_id: int, *, admin_only: bool = False) -> bool:
        allowed = await is_admin(interaction.user, interaction.guild) if admin_only else await can_manage(interaction.user, interaction.guild, owner_id)
        if not allowed:
            await interaction.followup.send("You are not allowed to manage this VPS.", ephemeral=True)
        return allowed

    async def _load(self, owner_id: int, container_id: str | None = None):
        record = (
            await store.get_user_by_container(container_id)
            if container_id
            else await store.get_user(owner_id)
        )
        if record and record["owner_id"] != owner_id:
            record = None
        if not record:
            raise LookupError("This VPS record has been deleted.")
        container = await docker_service.get(record["container_id"])
        await docker_service.reload(container)
        return record, container

    async def _refresh_panel(
        self,
        interaction: discord.Interaction,
        owner_id: int,
        container_id: str | None,
    ) -> None:
        record, container = await self._load(owner_id, container_id)
        live = None
        if status_name(container).lower() == "running":
            memory_limit = container_specs(container, record)[0] * 1024**2
            live = stats_values(
                await docker_service.stats(container),
                fallback_memory_limit=memory_limit,
            )
        await interaction.message.edit(
            content=None,
            embeds=[],
            view=ManagementView(record, container, live),
        )

    async def perform(self, interaction: discord.Interaction, action: Literal["start", "stop", "restart", "stats", "sshx", "delete"]) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        owner_id = await self._owner_id(interaction)
        if owner_id is None:
            await interaction.followup.send("This panel is malformed; open a new one with `>manage`.", ephemeral=True)
            return
        container_id = await self._container_id(interaction)
        if not await self._authorized(interaction, owner_id, admin_only=action == "delete"):
            return
        try:
            if action == "stats":
                await interaction.followup.send(
                    view=await build_stats_view(owner_id, container_id=container_id),
                    ephemeral=True,
                )
                return
            if action == "sshx":
                cog = interaction.client.get_cog("SSHXCog")
                if cog is None:
                    raise RuntimeError("SSHX service is unavailable.")
                await cog.start_for_interaction(interaction, owner_id, container_id)
                return
            record, container = await self._load(owner_id, container_id)
            state = status_name(container).lower()
            if action == "start":
                if state == "running":
                    await interaction.followup.send("⚠️ The container is already running.", ephemeral=True)
                    return
                await docker_service.action(container, "start")
                await store.update_user(
                    owner_id,
                    record["container_id"],
                    {"last_started_at": utcnow().isoformat()},
                )
                message = "▶ Container started."
            elif action == "stop":
                if state != "running":
                    await interaction.followup.send("⚠️ The container is already stopped.", ephemeral=True)
                    return
                await docker_service.action(container, "stop")
                message = "⏹ Container stopped."
            elif action == "restart":
                await docker_service.action(container, "restart")
                await store.update_user(
                    owner_id,
                    record["container_id"],
                    {"last_started_at": utcnow().isoformat()},
                )
                message = "🔄 Container restarted."
            else:  # delete
                await docker_service.remove(container)
                await store.remove_user(owner_id, record["container_id"])
                await interaction.message.edit(
                    content=None,
                    embeds=[],
                    view=text_view(
                        "🗑️ VPS Deleted",
                        f"`{record['container_name']}` was removed by {interaction.user.mention}.",
                        color=discord.Color.red(),
                    ),
                )
                await interaction.followup.send("Container and stored record deleted.", ephemeral=True)
                return
            await asyncio.sleep(0.5)
            await self._refresh_panel(interaction, owner_id, container_id)
            await interaction.followup.send(message, ephemeral=True)
        except LookupError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(**docker_error_kwargs(exc), ephemeral=True)

class ManageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="manage")
    @commands.guild_only()
    async def manage(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        vps_number: int = 1,
    ) -> None:
        """Open the persistent control panel for your VPS, or an admin-selected VPS."""
        target = member or ctx.author
        if not await can_manage(ctx.author, ctx.guild, target.id):
            await ctx.send("You can only open your own management panel.")
            return
        record = await store.get_user(target.id, vps_number)
        if not record:
            await ctx.send(f"VPS #{vps_number} is not registered for {target.mention}.")
            return
        try:
            container = await docker_service.get(record["container_id"])
            await docker_service.reload(container)
            live = None
            if status_name(container).lower() == "running":
                memory_limit = container_specs(container, record)[0] * 1024**2
                live = stats_values(
                    await docker_service.stats(container),
                    fallback_memory_limit=memory_limit,
                )
            await ctx.send(view=ManagementView(record, container, live))
        except Exception as exc:
            await ctx.send(**docker_error_kwargs(exc))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ManageCog(bot))
