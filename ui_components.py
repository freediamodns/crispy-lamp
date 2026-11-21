from __future__ import annotations

import discord


def text_view(title: str, body: str, *, color: discord.Color | None = None) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {title}\n\n{body}"),
            accent_color=color or discord.Color.blurple(),
        )
    )
    return view


def suspended_vps_view() -> discord.ui.LayoutView:
    return text_view(
        "⚠️ VPS Suspended",
        "This VPS has been suspended for using QEMU.\n"
        "**Suspended by:** AutoMod\n"
        "Create a ticket to request unsuspension.",
        color=discord.Color.yellow(),
    )
