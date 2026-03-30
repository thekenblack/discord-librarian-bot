"""
AI 사서봇 엔트리포인트
"""

import os
import sys
import logging
import zoneinfo
from datetime import datetime, timezone
from config import AI_BOT_TOKEN, GEMINI_API_KEYS, AI_NAME, AI_STATUS_TEXT, LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)

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
    """날짜별 파일에 직접 쓰는 핸들러: bot.2026-03-31.log"""
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
