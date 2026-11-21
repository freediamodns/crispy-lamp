from __future__ import annotations

import discord
from discord.ext import commands

from services import BOT_OWNER_ID, store
from ui_components import text_view


def bot_owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.author.id == BOT_OWNER_ID
    return commands.check(predicate)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.group(name="admin", invoke_without_command=True)
    @commands.guild_only()
    async def admin(self, ctx: commands.Context) -> None:
        await ctx.send("Use `>admin add @user` or `>admin remove @user`.")

    @admin.command(name="add")
    @bot_owner_only()
    async def add(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.id == ctx.guild.owner_id:
            await ctx.send("The server owner already has full permissions.")
            return
        await store.add_admin(member.id)
        await ctx.send(f"✅ {member.mention} is now a bot admin.")

    @admin.command(name="remove")
    @bot_owner_only()
    async def remove(self, ctx: commands.Context, member: discord.Member) -> None:
        await store.remove_admin(member.id)
        await ctx.send(f"✅ Removed {member.mention} from bot admins.")

    @commands.command(name="specs")
    @commands.guild_only()
    @bot_owner_only()
    async def specs(self, ctx: commands.Context, ram_mb: str, cores: str) -> None:
        """Set the per-VPS RAM (MB) and CPU core limits for this server."""
        try:
            ram_limit = int(ram_mb)
            core_limit = int(cores)
        except ValueError:
            await ctx.send("Usage: `>specs <ram_mb> <cores>` (both values must be positive whole numbers).")
            return
        if ram_limit < 1 or core_limit < 1:
            await ctx.send("RAM and CPU core limits must both be positive.")
            return
        await store.set_specs(ctx.guild.id, ram_limit, core_limit)
        await ctx.send(
            f"✅ New VPSes will be limited to **{ram_limit} MB RAM** and **{core_limit} CPU cores**. "
            "Existing VPS limits are unchanged."
        )

    @commands.command(name="admins")
    @commands.guild_only()
    async def admins(self, ctx: commands.Context) -> None:
        ids = sorted(await store.admins())
        if not ids:
            await ctx.send("There are no configured bot admins. The server owner always has access.")
            return
        body = "\n".join(f"• <@{user_id}> (`{user_id}`)" for user_id in ids)
        await ctx.send(view=text_view("🛡️ KTS Bot Admins", body + "\n\n-# Server owner permissions are automatic and are not listed here."))

    @commands.command(name="help")
    @commands.guild_only()
    async def help(self, ctx: commands.Context) -> None:
        body = (
            "**VPS commands**\n"
            "`>manage [@user] [vps_number]` — Open a VPS control panel.\n"
            "`>stats [@user] [vps_number]` — View live VPS usage.\n"
            "`>sshx [@user] [vps_number]` — Get a private SSHX terminal link by DM.\n"
            "`>delete @user [vps_number]` — Delete a VPS (bot owner, server owner, or bot admin).\n"
            "`git clone <repo_url>` — Download a Git repository into your VPS.\n"
            "`git pull` — Update a cloned repository from inside its directory.\n"
            "`>admins` — List bot admins.\n\n"
            "**Bot owner** (`1481235590283530312`)\n"
            "`>specs <ram_mb> <cores>` — Set limits for new VPSes, e.g. `>specs 127000 24`.\n"
            "`>admin add @user` / `>admin remove @user` — Manage bot admins.\n\n"
            "**Bot owner / server owner / bot admin**\n"
            "`>create @user` — Create a VPS for a member.\n\n"
            "**Access**\n"
            "VPS owners can manage their own VPSes. The bot owner, server owner, and bot admins can manage other members' VPSes, "
            "delete VPSes, and request SSHX for a member. Only the bot owner ID above can change specs or manage bot admins."
        )
        await ctx.send(view=text_view("📖 KTS Bot Help", body))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
