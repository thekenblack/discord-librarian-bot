"""
AI 사서봇 엔트리포인트
"""

import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from config import AI_BOT_TOKEN, GEMINI_API_KEYS, AI_NAME, AI_STATUS_TEXT, LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)

# 날짜별 로그 파일 (bot.log → bot.log.2026-03-30 으로 롤링)
file_handler = TimedRotatingFileHandler(
    os.path.join(LOG_DIR, "bot.log"),
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
file_handler.suffix = "%Y-%m-%d"

import zoneinfo
_tz_name = os.getenv("TZ", "Asia/Seoul")
try:
    _tz = zoneinfo.ZoneInfo(_tz_name)
except Exception:
    _tz = None

class _TZFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if _tz:
            dt = dt.astimezone(_tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

_formatter = _TZFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
file_handler.setFormatter(_formatter)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        stream_handler,
        file_handler,
    ],
)
logging.getLogger("discord.client").setLevel(logging.ERROR)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.ERROR)
logging.getLogger("discord.state").setLevel(logging.WARNING)

logger = logging.getLogger("AILibrarian")

if not AI_BOT_TOKEN:
    logger.warning("AI_BOT_TOKEN이 설정되지 않았습니다. AI 사서봇을 건너뜁니다.")
    sys.exit(0)

if not GEMINI_API_KEYS:
    logger.warning("GEMINI_API_KEYS가 설정되지 않았습니다. AI 사서봇을 건너뜁니다.")
    sys.exit(0)

logger.info(f"Gemini API 키 {len(GEMINI_API_KEYS)}개 로드됨")

PERSONA_DIR = os.path.dirname(os.path.abspath(__file__))

from librarian.persona import Persona
from librarian.core import AILibrarianBot

persona = Persona(PERSONA_DIR, AI_NAME, AI_STATUS_TEXT)
bot = AILibrarianBot(persona, GEMINI_API_KEYS)

if __name__ == "__main__":
    bot.run(AI_BOT_TOKEN)
