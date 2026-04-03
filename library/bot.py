"""
Discord Library Bot
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
import traceback
import sys
from library.db import LibraryDB
from config import BOT_TOKEN, GUILD_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("discord.client").setLevel(logging.ERROR)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.state").setLevel(logging.WARNING)
logger = logging.getLogger("LibraryBot")

intents = discord.Intents.default()
intents.message_content = True


class BotCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if getattr(bot, "stopped", False):
            cmd_name = (interaction.data or {}).get("name", "")
            if cmd_name != "admin":
                await interaction.response.send_message(
                    "봇이 일시 중지 상태입니다.", ephemeral=True
                )
                return False
        return True


class LibraryBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, tree_cls=BotCommandTree)
        self.db = LibraryDB()
        self.stopped = False

    async def on_ready(self):
        logger.info(f"{self.user} 온라인!")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name="Library"
        ))

    async def on_error(self, event: str, *args, **kwargs):
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type is None:
            return
        tb = "".join(traceback.format_exc())
        logger.error(f"[이벤트 오류] '{event}':\n{tb}")

    async def setup_hook(self):
        # 슬래시 커맨드 에러 핸들러
        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: Exception):
            origin = getattr(error, "original", error)
            cmd = interaction.command.qualified_name if interaction.command else "unknown"

            if isinstance(origin, app_commands.CheckFailure):
                return

            tb = "".join(traceback.format_exception(type(origin), origin, origin.__traceback__))
            logger.error(f"[커맨드 오류] /{cmd}:\n{tb}")

        logger.info("[1/4] 데이터베이스 초기화 중...")
        await self.db.init()

        logger.info("[2/4] BotView 설정 중...")
        from library.utils import BotView
        BotView._bot_ref = self

        logger.info("[3/5] Cog 로딩 중...")
        from library.cogs.commands import LibraryCog
        from library.cogs.admin import AdminCog
        from library.cogs.shop import ShopCog
        await self.add_cog(LibraryCog(self))
        await self.add_cog(AdminCog(self))
        await self.add_cog(ShopCog(self))

        logger.info("[4/5] 슬래시 커맨드 동기화 중...")
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(f"[5/5] 동기화 완료 (서버 {GUILD_ID}) - {len(synced)}개: {[c.name for c in synced]}")
        else:
            synced = await self.tree.sync()
            logger.info(f"[5/5] 동기화 완료 (전체) - {len(synced)}개: {[c.name for c in synced]}")


bot = LibraryBot()

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
    if getattr(bot, "restart_on_exit", False):
        sys.exit(42)
