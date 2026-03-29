import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID      = os.getenv("DISCORD_GUILD_ID", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "librarian_bot.db")
UPLOAD_DIR    = os.getenv("UPLOAD_DIR", "uploads")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(25 * 1024 * 1024)))  # 25MB

# 어드민 유저 ID 목록 (쉼표 구분)
ADMIN_IDS = [uid.strip() for uid in os.getenv("ADMIN_USER_IDS", "").split(",") if uid.strip()]

# ── AI 사서봇 ────────────────────────────────
AI_BOT_TOKEN   = os.getenv("AI_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PERSONA_PATH   = os.getenv("PERSONA_PATH", "ai/persona.json")
