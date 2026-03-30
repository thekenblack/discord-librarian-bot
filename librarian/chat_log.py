"""
구조화된 대화 로그 (JSON Lines)
logs/chat.jsonl에 기록. 버전별 비교 분석용.
"""

import json
import os
import hashlib
from datetime import datetime, timezone
from config import LOG_DIR, VERSION, GIT_HASH, GEMINI_MODEL

_LOG_PATH = os.path.join(LOG_DIR, "chat.jsonl")


def _prompt_hash(prompt: str) -> str:
    """프롬프트 텍스트의 짧은 해시 (버전 간 프롬프트 변경 감지용)"""
    return hashlib.sha256(prompt.encode()).hexdigest()[:12]


def log_startup(persona_name: str, prompt_text: str, api_key_count: int):
    """봇 시작 시 버전 정보 기록"""
    _write({
        "event": "startup",
        "version": VERSION,
        "git": GIT_HASH,
        "model": GEMINI_MODEL,
        "persona": persona_name,
        "prompt_hash": _prompt_hash(prompt_text),
        "api_keys": api_key_count,
    })


def log_chat(
    *,
    guild: str,
    channel: str,
    user_id: str,
    user_name: str,
    user_text: str,
    reply_text: str,
    tools_called: list[str],
    tool_results: list[str],
    has_file: bool,
    retries: int,
    web_search: bool,
    error: str | None = None,
):
    """대화 1건 기록"""
    _write({
        "event": "chat",
        "version": VERSION,
        "model": GEMINI_MODEL,
        "guild": guild,
        "channel": channel,
        "user_id": user_id,
        "user_name": user_name,
        "input": user_text[:500],
        "output": reply_text[:500],
        "tools": tools_called,
        "tool_results": [r[:200] for r in tool_results],
        "file": has_file,
        "retries": retries,
        "web_search": web_search,
        "error": error,
    })


def _write(data: dict):
    """JSON Lines 한 줄 추가"""
    import os, zoneinfo
    try:
        tz = zoneinfo.ZoneInfo(os.getenv("TZ", "Asia/Seoul"))
    except Exception:
        tz = timezone.utc
    data["ts"] = datetime.now(tz).isoformat()
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    with open(_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
