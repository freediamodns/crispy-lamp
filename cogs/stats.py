from __future__ import annotations

import discord
from discord.ext import commands

from services import (
    can_manage,
    container_specs,
    docker_error_kwargs,
    docker_service,
    stats_values,
    status_name,
    store,
    uptime,
)
from ui_components import text_view


async def build_stats_view(
    owner_id: int,
    vps_number: int = 1,
    container_id: str | None = None,
) -> discord.ui.LayoutView:
    record = (
        await store.get_user_by_container(container_id)
        if container_id
        else await store.get_user(owner_id, vps_number)
    )
    if record and record["owner_id"] != owner_id:
        record = None
    if not record:
        name = "This VPS" if container_id else f"VPS #{vps_number}"
        raise LookupError(f"{name} is not registered for this user.")
    container = await docker_service.get(record["container_id"])
    await docker_service.reload(container)
    if status_name(container).lower() != "running":
        raise RuntimeError("The container must be running to retrieve live statistics.")
    memory_limit = container_specs(container, record)[0] * 1024**2
    values = stats_values(
        await docker_service.stats(container),
        fallback_memory_limit=memory_limit,
    )
    body = "\n".join(
        (
            f"**CPU:** {values['cpu']}",
            f"**RAM Usage:** {values['ram']}",
            f"**RAM Limit:** {values['limit']}",
            f"**Network RX:** {values['rx']}",
            f"**Network TX:** {values['tx']}",
            f"**Uptime:** {uptime(container, record.get('last_started_at') or record.get('created_at'), fallback_label='last start' if record.get('last_started_at') else 'creation')}",
            f"**Status:** {status_name(container)}",
            "",
            f"-# Container: {record['container_name']} • Refresh using the panel",
        )
    )
    return text_view("📊 Live VPS Statistics", body, color=discord.Color.teal())


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="stats")
    @commands.guild_only()
    async def stats(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        vps_number: int = 1,
    ) -> None:
        target = member or ctx.author
        if not await can_manage(ctx.author, ctx.guild, target.id):
            await ctx.send("You can only view your own VPS statistics.")
            return
        try:
            await ctx.send(view=await build_stats_view(target.id, vps_number))
        except (LookupError, RuntimeError) as exc:
            await ctx.send(str(exc))
        except Exception as exc:
            await ctx.send(**docker_error_kwargs(exc))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
