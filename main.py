"""
통합 봇 엔트리포인트
라이브러리 봇 + AI 사서봇을 하나의 프로세스에서 실행.
"""

import asyncio
import os
import sys
import logging
import zoneinfo
from datetime import datetime, timezone
from config import (
    BOT_TOKEN, AI_BOT_TOKEN, GEMINI_API_KEY, GUILD_ID,
    AI_NAME, AI_STATUS_TEXT, LOG_DIR,
)

os.makedirs(LOG_DIR, exist_ok=True)

# ── 로깅 설정 ──────────────────────────────────────

_tz_name = os.getenv("TZ", "Asia/Seoul")
try:
    _tz = zoneinfo.ZoneInfo(_tz_name)
except Exception:
    _tz = None

def _now():
    dt = datetime.now(timezone.utc)
    return dt.astimezone(_tz) if _tz else dt

class _TZFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if _tz:
            dt = dt.astimezone(_tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

class _DailyFileHandler(logging.FileHandler):
    def __init__(self, log_dir, prefix="bot", encoding="utf-8"):
        self._log_dir = log_dir
        self._prefix = prefix
        self._current_date = None
        path = self._get_path()
        super().__init__(path, encoding=encoding)

    def _get_path(self):
        date_str = _now().strftime("%Y-%m-%d")
        self._current_date = date_str
        return os.path.join(self._log_dir, f"{self._prefix}.{date_str}.log")

    def emit(self, record):
        date_str = _now().strftime("%Y-%m-%d")
        if date_str != self._current_date:
            self.close()
            self.baseFilename = self._get_path()
            self.stream = self._open()
        super().emit(record)

_formatter = _TZFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

file_handler = _DailyFileHandler(LOG_DIR, prefix="bot")
file_handler.setFormatter(_formatter)

from config import GIT_HASH
_commit_log_name = f"bot.{_now().strftime('%Y-%m-%d')}_{GIT_HASH}.log"
_commit_log_path = os.path.join(LOG_DIR, _commit_log_name)
commit_handler = logging.FileHandler(_commit_log_path, encoding="utf-8")
commit_handler.setFormatter(_formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[stream_handler, file_handler, commit_handler],
)
logging.getLogger("discord.client").setLevel(logging.ERROR)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.state").setLevel(logging.WARNING)

logger = logging.getLogger("Combined")

# ── 봇 인스턴스 생성 ──────────────────────────────

from library.bot import LibraryBot
library_bot = LibraryBot()

ai_bot = None
if AI_BOT_TOKEN and GEMINI_API_KEY:
    import importlib as _il
    Persona = _il.import_module("librarian.layers.03_character.persona").Persona
    from librarian.core import AILibrarianBot

    PERSONA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "librarian")
    persona = Persona(PERSONA_DIR, AI_NAME, AI_STATUS_TEXT)
    ai_bot = AILibrarianBot(persona, GEMINI_API_KEY)

    # 상호 참조: 서로의 클라이언트에 직접 접근 가능
    ai_bot.library_bot_client = library_bot
    library_bot.ai_bot_client = ai_bot
    logger.info("AI 사서봇 초기화 완료")
else:
    logger.warning("AI_BOT_TOKEN 또는 GEMINI_API_KEY 미설정 — AI 사서봇 비활성")


async def main():
    restart = False
    try:
        tasks = [asyncio.create_task(library_bot.start(BOT_TOKEN))]
        if ai_bot:
            tasks.append(asyncio.create_task(ai_bot.start(AI_BOT_TOKEN)))
        # 어느 한쪽이 끝나면 전체 종료
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except KeyboardInterrupt:
        pass
    finally:
        if not library_bot.is_closed():
            await library_bot.close()
        if ai_bot and not ai_bot.is_closed():
            await ai_bot.close()
        if getattr(library_bot, "restart_on_exit", False) or \
           (ai_bot and getattr(ai_bot, "restart_on_exit", False)):
            restart = True

    if restart:
        sys.exit(42)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
