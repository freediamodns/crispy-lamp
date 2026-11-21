"""
DM-All announcement bot (discord.py 2.x)

Setup:
  pip install -U discord.py python-dotenv
  Create a .env file next to this script:  DISCORD_TOKEN=your_bot_token
  In the Discord Developer Portal -> Bot -> enable "SERVER MEMBERS INTENT".
  Invite the bot with scopes: bot + applications.commands

Commands:
  /dmall message:<text>  (Administrators only) - DMs every non-bot member, with a confirm step
  /optout                (anyone)               - stop receiving /dmall messages
  /optin                 (anyone)               - start receiving them again
"""

import asyncio
import json
import os
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

DELAY_SECONDS = 2.0  # pause between DMs to stay under Discord's rate limits
OPTOUT_FILE = Path("optout.json")

intents = discord.Intents.default()
intents.members = True  # required to list server members


def load_optouts() -> set[int]:
    try:
        return set(json.loads(OPTOUT_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_optouts(ids: set[int]) -> None:
    OPTOUT_FILE.write_text(json.dumps(sorted(ids)))


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.running_guilds: set[int] = set()  # prevents two sends at once per server

    async def setup_hook(self):
        await self.tree.sync()


bot = Bot()


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Send", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, _):
        self.confirmed = True
        await interaction.response.edit_message(content="Sending...", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _):
        self.confirmed = False
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


@bot.tree.command(name="dmall", description="DM every member of this server (admins only)")
@app_commands.describe(message="The message to send")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def dmall(interaction: discord.Interaction, message: str):
    guild = interaction.guild

    if guild.id in bot.running_guilds:
        await interaction.response.send_message("A send is already in progress.", ephemeral=True)
        return

    optouts = load_optouts()
    targets = [m for m in guild.members if not m.bot and m.id not in optouts]
    eta = int(len(targets) * DELAY_SECONDS / 60) + 1

    view = ConfirmView(interaction.user.id)
    await interaction.response.send_message(
        f"This will DM **{len(targets)}** members (about {eta} min).\n\n"
        f"**Preview:**\n{message}\n\nProceed?",
        view=view,
        ephemeral=True,
    )
    await view.wait()
    if not view.confirmed:
        return

    bot.running_guilds.add(guild.id)
    sent = failed = 0
    footer = f"\n\n-# Sent from {guild.name}. Use /optout in the server to stop these messages."
    try:
        for member in targets:
            try:
                await member.send(message + footer)
                sent += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1  # DMs closed or blocked the bot
            await asyncio.sleep(DELAY_SECONDS)
    finally:
        bot.running_guilds.discard(guild.id)

    # Interaction tokens expire after 15 min, so report via the channel instead.
    if interaction.channel:
        await interaction.channel.send(
            f"{interaction.user.mention} DM broadcast finished: "
            f"**{sent}** delivered, **{failed}** failed (DMs closed)."
        )


@dmall.error
async def dmall_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Administrators only.", ephemeral=True)
    else:
        raise error


@bot.tree.command(name="optout", description="Stop receiving server announcement DMs")
async def optout(interaction: discord.Interaction):
    ids = load_optouts()
    ids.add(interaction.user.id)
    save_optouts(ids)
    await interaction.response.send_message("You won't receive announcement DMs anymore.", ephemeral=True)


@bot.tree.command(name="optin", description="Receive server announcement DMs again")
async def optin(interaction: discord.Interaction):
    ids = load_optouts()
    ids.discard(interaction.user.id)
    save_optouts(ids)
    await interaction.response.send_message("You'll receive announcement DMs again.", ephemeral=True)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env file.")
    bot.run(TOKEN)