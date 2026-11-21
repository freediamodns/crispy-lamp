from __future__ import annotations

import asyncio
import re

import discord
from discord.ext import commands

from services import can_manage, docker_error_kwargs, docker_service, status_name, store
from ui_components import text_view

URL_PATTERN = re.compile(r"https?://(?:www\.)?sshx\.io/[^\s'\"]+", re.IGNORECASE)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def result_text(result) -> str:
    output = getattr(result, "output", b"")
    if isinstance(output, tuple):
        output = b"".join(part or b"" for part in output)
    return output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output or "")


class SSHXCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _start(
        self,
        owner_id: int,
        vps_number: int = 1,
        container_id: str | None = None,
    ) -> tuple[dict, str]:
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
            raise RuntimeError("Start the container before requesting SSHX.")

        # All commands run as the default root user in the Ubuntu image.
        curl_check = await asyncio.wait_for(docker_service.exec(container, "command -v curl"), timeout=15)
        if getattr(curl_check, "exit_code", 1) != 0:
            install = "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq curl"
            install_result = await asyncio.wait_for(docker_service.exec(container, install), timeout=120)
            if getattr(install_result, "exit_code", 1) != 0:
                raise RuntimeError("Automatic curl installation failed. Review the Docker/container network configuration.")
            verified = await asyncio.wait_for(docker_service.exec(container, "command -v curl"), timeout=15)
            if getattr(verified, "exit_code", 1) != 0:
                raise RuntimeError("curl could not be verified after installation.")

        # SSHX is intentionally detached: never wait for its long-running terminal process.
        command = "cd /root && rm -f /root/sshx.log && nohup sh -c 'curl -sSf https://sshx.io/get | sh -s run' > /root/sshx.log 2>&1 &"
        await docker_service.exec(container, command, detach=True)
        await asyncio.sleep(6)
        log_result = await asyncio.wait_for(docker_service.exec(container, "tail -n 80 /root/sshx.log 2>/dev/null || true"), timeout=15)
        logs = ANSI_ESCAPE_PATTERN.sub("", result_text(log_result)).strip()
        urls = URL_PATTERN.findall(logs)
        if not urls:
            raise RuntimeError("SSHX started but did not publish a link yet. Try again in a few seconds; no credentials were posted publicly.")
        # Only the URL is returned to Discord. Installer output is retained
        # in /root/sshx.log inside the container for troubleshooting.
        return record, urls[-1]

    async def start_for_interaction(
        self,
        interaction: discord.Interaction,
        owner_id: int,
        container_id: str | None = None,
    ) -> None:
        try:
            record, url = await self._start(owner_id, container_id=container_id)
            view = text_view(
                "🔑 Your private SSHX session",
                f"**SSHX Link:** {url}\n\n-# Keep this link private. Anyone with it may access the terminal session.",
                color=discord.Color.gold(),
            )
            try:
                await interaction.user.send(view=view)
            except discord.Forbidden:
                await interaction.followup.send("I could not DM you. Enable DMs from this server and request SSHX again.", ephemeral=True)
                return
            await interaction.followup.send("🔐 SSHX credentials were sent to your DMs.", ephemeral=True)
        except (LookupError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("SSHX setup timed out safely. The bot did not wait indefinitely; please retry.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(**docker_error_kwargs(exc), ephemeral=True)

    @commands.command(name="sshx")
    @commands.guild_only()
    async def sshx(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        vps_number: int = 1,
    ) -> None:
        target = member or ctx.author
        if not await can_manage(ctx.author, ctx.guild, target.id):
            await ctx.send("You can only request SSHX for your own VPS.")
            return
        progress = await ctx.send("Preparing a private SSHX session…")
        try:
            record, url = await self._start(target.id, vps_number)
            view = text_view(
                "🔑 Your private SSHX session",
                f"**SSHX Link:** {url}\n\n-# Keep this link private. Anyone with it may access the terminal session.",
                color=discord.Color.gold(),
            )
            await ctx.author.send(view=view)
            await progress.edit(content="🔐 SSHX credentials were sent to your DMs.")
        except discord.Forbidden:
            await progress.edit(content="I could not DM you. Enable DMs from this server and retry.")
        except (LookupError, RuntimeError) as exc:
            await progress.edit(content=str(exc))
        except asyncio.TimeoutError:
            await progress.edit(content="SSHX setup timed out safely. Please retry.")
        except Exception as exc:
            await progress.edit(**docker_error_kwargs(exc, edit=True))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SSHXCog(bot))
