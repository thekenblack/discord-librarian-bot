"""
AI 사서봇 엔트리포인트
"""

import sys
import logging
from config import AI_BOT_TOKEN, GEMINI_API_KEYS, AI_NAME, AI_STATUS_TEXT, AI_PERSONA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ai_bot.log", encoding="utf-8"),
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

from ai.persona import Persona
from ai.bot import AILibrarianBot

persona = Persona(AI_PERSONA_DIR, AI_NAME, AI_STATUS_TEXT)
bot = AILibrarianBot(persona, GEMINI_API_KEYS)

if __name__ == "__main__":
    bot.run(AI_BOT_TOKEN)
