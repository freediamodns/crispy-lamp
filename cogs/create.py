from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands
from docker.errors import APIError

from services import (
    BANNER_FILE,
    BOT_OWNER_ID,
    DockerService,
    container_specs,
    display_bytes,
    DISPLAY_HOST_CORES,
    DISPLAY_HOST_RAM,
    docker_error_kwargs,
    docker_service,
    is_admin,
    store,
)
from ui_components import text_view

UTC = timezone.utc


class CreateCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="create")
    @commands.guild_only()
    async def create(self, ctx: commands.Context, member: discord.Member) -> None:
        """Create the managed Ubuntu VPS for a member (owner/admin only)."""
        if ctx.author.id != BOT_OWNER_ID and not await is_admin(ctx.author, ctx.guild):
            await ctx.send("Only the bot owner, server owner, or a bot admin can create containers.")
            return
        ram_mb, cores = await store.get_specs(ctx.guild.id)
        async with store.user_lock(member.id):
            existing = await store.get_users(member.id)
            vps_number = max((record["vps_number"] for record in existing), default=0) + 1
            if existing:
                status = await ctx.send(
                    f"⚠️ {member.mention} already has {len(existing)} VPS. "
                    f"Creating VPS #{vps_number}…"
                )
            else:
                status = await ctx.send(f"Creating an Ubuntu 24.04 VPS for {member.mention}…")
            container = None
            newly_created = False
            try:
                container, newly_created = await docker_service.create(member.id, vps_number, ram_mb, cores)
                await docker_service.reload(container)
                actual_ram_mb, actual_cores = container_specs(container)
                record = {
                    "owner_id": member.id,
                    "owner_name": str(member),
                    "container_name": container.name,
                    "container_id": container.id,
                    "vps_number": vps_number,
                    "image": DockerService.IMAGE,
                    "ram_mb": actual_ram_mb,
                    "cores": actual_cores,
                    "created_at": datetime.now(UTC).isoformat(),
                }
                await store.put_user(member.id, record)
            except APIError as exc:
                if getattr(exc.response, "status_code", None) == 409:
                    suffix = "" if vps_number == 1 else f"-{vps_number - 1}"
                    await status.edit(
                        content=(
                            f"Docker reports that `user_{member.id}{suffix}` already exists, but it is "
                            "not a managed container that can be safely reused. Stopped containers "
                            "are hidden by `docker ps`; check `docker ps -a` and confirm the bot is "
                            "connected to the same Docker daemon/context."
                        )
                    )
                else:
                    await status.edit(**docker_error_kwargs(exc, edit=True))
                return
            except RuntimeError as exc:
                if container is not None and newly_created:
                    await docker_service.remove(container)
                message = f"VPS provisioning failed: {exc}"
                message += " The incomplete container was removed." if newly_created else " The existing container was left intact."
                await status.edit(content=message)
                return
            except Exception as exc:
                if container is not None and newly_created:
                    try:
                        await docker_service.remove(container)
                    except Exception:
                        pass
                await status.edit(**docker_error_kwargs(exc, edit=True))
                return

            body = "\n".join(
                (
                    f"VPS created for {member.mention}.",
                    f"**Owner:** {member.mention}",
                    f"**Container:** `{container.name}` (`{container.short_id}`)",
                    f"**VPS:** #{vps_number}",
                    "**OS:** Ubuntu 24.04",
                    "**Status:** Running",
                    f"**Host Capacity:** {DISPLAY_HOST_RAM} RAM • {DISPLAY_HOST_CORES} cores",
                    f"**VPS RAM Limit:** {display_bytes(actual_ram_mb * 1024**2)}",
                    f"**VPS CPU Limit:** {actual_cores:g} cores",
                    f"**Created:** {discord.utils.format_dt(datetime.now(UTC), 'F')}",
                    "**Features:** systemd • privileged Docker-in-Docker/Wings • host networking • curl + nano",
                    "",
                    "Use `>manage` to open the controls.",
                )
            )
            view = text_view("✅ VPS Created", body, color=discord.Color.green())
            attachments = []
            if BANNER_FILE.exists():
                view.children[0].add_item(
                    discord.ui.MediaGallery(
                        discord.MediaGalleryItem(
                            "attachment://banner.png",
                            description="KTS Host VPS",
                        )
                    )
                )
                attachments.append(discord.File(BANNER_FILE, filename="banner.png"))
            await status.edit(
                content=None,
                embeds=[],
                attachments=attachments,
                view=view,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CreateCog(bot))
