"""
서버 메시지 로그 (logs/server.log)
비트쨩에 대한 유저 반응/평가를 나중에 분석하기 위한 용도.
봇 멘션 여부와 무관하게 서버의 모든 메시지를 기록한다.
"""

import os
import logging
from config import LOG_DIR

os.makedirs(LOG_DIR, exist_ok=True)

_logger = logging.getLogger("ServerLog")
_logger.setLevel(logging.INFO)
_logger.propagate = False

_handler = logging.FileHandler(
    os.path.join(LOG_DIR, "server.log"),
    encoding="utf-8",
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger.addHandler(_handler)


def log(*, guild: str, channel: str, author: str, content: str, is_bot: bool = False):
    tag = "[BOT]" if is_bot else ""
    _logger.info(f"[{guild}/#{channel}] {author}{tag}: {content}")
