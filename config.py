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
MEDIA_DIR   = os.path.join(BASE_DIR, "librarian", "media")
LOG_DIR     = os.path.join(BASE_DIR, _paths.get("logs_dir", "logs"))
BACKUP_DIR  = os.path.join(BASE_DIR, _paths.get("backups_dir", "data/backups"))

# DB
_db = _conf.get("db", {})
LIBRARY_DB_PATH   = os.path.join(DATA_DIR, _db.get("library", "library.db"))
LIBRARIAN_DB_PATH = os.path.join(DATA_DIR, _db.get("librarian", "librarian.db"))
CHROMA_DIR        = os.path.join(DATA_DIR, "chroma")

# 파일
MAX_FILE_SIZE = _conf.get("max_file_size", 10 * 1024 * 1024)

# 하위 호환: UPLOAD_DIR → FILES_DIR
UPLOAD_DIR = FILES_DIR

# ── .env 비밀값 ─────────────────────────────────
BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID      = os.getenv("DISCORD_GUILD_ID", "")
AI_BOT_TOKEN  = os.getenv("AI_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GEMINI_API_KEYS", "").split(",")[0].strip())
ADMIN_IDS = [uid.strip() for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()]
LIGHTNING_ADDRESS = os.getenv("LIGHTNING_ADDRESS", "")

# ── Blink Lightning (충전 시스템) ──────────────
BLINK_API_KEY = os.getenv("BLINK_API_KEY", "")
BLINK_API_URL = os.getenv("BLINK_API_URL", "https://api.blink.sv/graphql")
MIN_CHARGE_SAT = int(os.getenv("MIN_CHARGE_SAT", "100"))
INVOICE_EXPIRE = int(os.getenv("INVOICE_EXPIRE", "3600"))

# ── 자발적 발화 ────────────────────────────────
SPONTANEOUS_CHANNEL_ID = os.getenv("SPONTANEOUS_CHANNEL_ID", "")
SPONTANEOUS_QUIET_HOURS = int(os.getenv("SPONTANEOUS_QUIET_HOURS", "3"))      # 최소 침묵 시��
SPONTANEOUS_CHECK_HOURS = int(os.getenv("SPONTANEOUS_CHECK_HOURS", "3"))      # 체크 간격
SPONTANEOUS_CHANCE = int(os.getenv("SPONTANEOUS_CHANCE", "25"))               # 발화 확률 (%)

# ── AI 설정 ───────────────────────────────────
_ai = _conf.get("ai", {})
GEMINI_MODEL     = _ai.get("model", "gemini-2.5-flash-lite")
GEMINI_MODEL_L2  = _ai.get("model_l2", GEMINI_MODEL)
GEMINI_MODEL_L4  = _ai.get("model_l4", GEMINI_MODEL)
AI_NAME          = os.getenv("AI_NAME", _ai.get("name", "사서봇"))
AI_STATUS_TEXT   = os.getenv("AI_STATUS_TEXT", _ai.get("status_text", "Library"))
AI_MAX_OUTPUT_TOKENS = _ai.get("max_output_tokens", 500)

# ── 프롬프트 ──────────────────────────────────
AI_HOURLY_WAGE   = int(os.getenv("AI_HOURLY_WAGE", "210"))
AI_CREATOR       = os.getenv("AI_CREATOR", "Ken")
AI_COMMUNITY     = os.getenv("AI_COMMUNITY", "시타델")
AI_COMMUNITY_DESC = os.getenv("AI_COMMUNITY_DESC", "비트코인 맥시멀리스트들의 요새")

# ── 레이어 온도 ───────────────────────────────
TEMP_L1 = float(os.getenv("TEMP_L1", "0.3"))
TEMP_L2 = float(os.getenv("TEMP_L2", "0.5"))
TEMP_L3 = float(os.getenv("TEMP_L3", "1.2"))
TEMP_L4 = float(os.getenv("TEMP_L4", "0.1"))
TEMP_L5 = float(os.getenv("TEMP_L5", "0.3"))

# ── 히스토리 ──────────────────────────────────
MAX_HISTORY_L1 = int(os.getenv("MAX_HISTORY_L1", "5"))
MAX_HISTORY_L3 = int(os.getenv("MAX_HISTORY_L3", "5"))
MAX_HISTORY_L5 = int(os.getenv("MAX_HISTORY_L5", "5"))
