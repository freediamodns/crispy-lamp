from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs.manage import ManagementView

load_dotenv()
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOG = logging.getLogger("ktsbot")


class KTSBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=">", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        # Stable custom IDs allow existing panels to continue working after restarts.
        self.add_view(ManagementView())
        for extension in ("cogs.create", "cogs.manage", "cogs.sshx", "cogs.admin", "cogs.stats"):
            await self.load_extension(extension)

    async def on_ready(self) -> None:
        LOG.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing argument: `{error.param.name}`. Use `>help` for command usage.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("I could not understand that argument. Please mention a valid user.")
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send("You do not have permission to use this command.")
            return
        LOG.exception("Unhandled command error", exc_info=error)
        await ctx.send("An unexpected error occurred. Please check the bot logs.")


async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Copy .env.example to .env and configure it.")
    async with KTSBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
