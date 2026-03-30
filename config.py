"""
설정 로더: config.json (프로젝트 구조) + .env (비밀값)
"""

import os
import json
import subprocess
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── config.json (커밋됨) ────────────────────────
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    _conf = json.load(f)

# ── 버전 ────────────────────────────────────────
VERSION = _conf.get("version", "0.0.0")

def get_git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"

GIT_HASH = get_git_hash()

# ── .env (비밀값) ───────────────────────────────
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── 경로 ────────────────────────────────────────
_paths = _conf.get("paths", {})
DATA_DIR    = os.path.join(BASE_DIR, _paths.get("data_dir", "data"))
FILES_DIR   = os.path.join(BASE_DIR, _paths.get("files_dir", "files"))
LOG_DIR     = os.path.join(BASE_DIR, _paths.get("logs_dir", "logs"))
BACKUP_DIR  = os.path.join(BASE_DIR, _paths.get("backups_dir", "data/backups"))

# DB
_db = _conf.get("db", {})
LIBRARY_DB_PATH   = os.path.join(DATA_DIR, _db.get("library", "library.db"))
LIBRARIAN_DB_PATH = os.path.join(DATA_DIR, _db.get("librarian", "librarian.db"))

# 파일
MAX_FILE_SIZE = _conf.get("max_file_size", 10 * 1024 * 1024)

# 하위 호환: UPLOAD_DIR → FILES_DIR
UPLOAD_DIR = FILES_DIR

# ── .env 비밀값 ─────────────────────────────────
BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID      = os.getenv("DISCORD_GUILD_ID", "")
AI_BOT_TOKEN  = os.getenv("AI_BOT_TOKEN", "")
GEMINI_API_KEYS = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
ADMIN_IDS = [uid.strip() for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()]
LIGHTNING_ADDRESS = os.getenv("LIGHTNING_ADDRESS", "")

# ── AI 설정 (config.json) ──────────────────────
_ai = _conf.get("ai", {})
GEMINI_MODEL     = _ai.get("model", "gemini-2.5-flash-lite")
AI_NAME          = os.getenv("AI_NAME", _ai.get("name", "사서봇"))
AI_STATUS_TEXT   = os.getenv("AI_STATUS_TEXT", _ai.get("status_text", "Library"))
AI_BUFFER_SIZE   = _ai.get("buffer_size", 30)
AI_MAX_OUTPUT_TOKENS = _ai.get("max_output_tokens", 500)
