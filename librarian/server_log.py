"""
서버 메시지 로그 (logs/server.log)
비트쨩에 대한 유저 반응/평가를 나중에 분석하기 위한 용도.
봇 멘션 여부와 무관하게 서버의 모든 메시지를 기록한다.
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler
from config import LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)

_logger = logging.getLogger("ServerLog")
_logger.setLevel(logging.INFO)
_logger.propagate = False

_handler = TimedRotatingFileHandler(
    os.path.join(LOG_DIR, "server.log"),
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
_handler.suffix = "%Y-%m-%d"

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

_handler.setFormatter(_TZFormatter("%(asctime)s %(message)s"))
_logger.addHandler(_handler)


def log(*, guild: str, channel: str, author: str, content: str, is_bot: bool = False):
    tag = "[BOT]" if is_bot else ""
    _logger.info(f"[{guild}/#{channel}] {author}{tag}: {content}")
