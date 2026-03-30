"""
서버 메시지 로그 (logs/server.YYYY-MM-DD.log)
비트쨩에 대한 유저 반응/평가를 나중에 분석하기 위한 용도.
봇 멘션 여부와 무관하게 서버의 모든 메시지를 기록한다.
"""

import os
import logging
import zoneinfo
from datetime import datetime, timezone
from config import LOG_DIR

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
    def __init__(self, log_dir, prefix="server", encoding="utf-8"):
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

_logger = logging.getLogger("ServerLog")
_logger.setLevel(logging.INFO)
_logger.propagate = False

_handler = _DailyFileHandler(LOG_DIR, prefix="server")
_handler.setFormatter(_TZFormatter("%(asctime)s %(message)s"))
_logger.addHandler(_handler)


def log(*, guild: str, channel: str, author: str, content: str, is_bot: bool = False):
    tag = "[BOT]" if is_bot else ""
    _logger.info(f"[{guild}/#{channel}] {author}{tag}: {content}")
