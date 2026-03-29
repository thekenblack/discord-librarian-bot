"""
Discord Embed 유틸리티
"""

import discord
from datetime import datetime, timezone

SUCCESS_COLOR = 0x2ECC71
ERROR_COLOR   = 0xE74C3C
INFO_COLOR    = 0x3498DB


def success_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=SUCCESS_COLOR)
    e.timestamp = datetime.now(timezone.utc)
    return e


def error_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=ERROR_COLOR)
    e.timestamp = datetime.now(timezone.utc)
    return e


def info_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=INFO_COLOR)
    e.timestamp = datetime.now(timezone.utc)
    return e


def file_size_fmt(size: int) -> str:
    """바이트 -> 읽기 좋은 문자열"""
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GB"


class BotView(discord.ui.View):
    """봇 정지 상태를 체크하는 공통 View 베이스 클래스"""
    _bot_ref = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if BotView._bot_ref and BotView._bot_ref.stopped:
            try:
                await interaction.response.send_message(
                    "봇이 일시 중지 상태입니다.", ephemeral=True
                )
            except Exception:
                pass
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        msg = getattr(self, "message", None) or getattr(self, "_message_ref", None)
        if msg:
            try:
                await msg.edit(view=self)
            except Exception:
                pass
