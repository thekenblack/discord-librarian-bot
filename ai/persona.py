"""
페르소나 + 시스템 프롬프트 로드 (txt 기반)
"""

import os
import random


def _load_lines(path: str) -> list[str]:
    """파일에서 빈 줄을 제외한 줄 목록 반환"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


class Persona:
    def __init__(self, persona_dir: str, name: str, status_text: str):
        self.name = name
        self.status_text = status_text

        # 페르소나 프롬프트 (캐릭터)
        persona_path = os.path.join(persona_dir, "persona.txt")
        persona_text = ""
        if os.path.exists(persona_path):
            with open(persona_path, encoding="utf-8") as f:
                persona_text = f.read().replace("{name}", name)

        # 시스템 프롬프트 (동작 규칙)
        prompt_path = os.path.join(persona_dir, "prompt.txt")
        prompt_text = ""
        if os.path.exists(prompt_path):
            with open(prompt_path, encoding="utf-8") as f:
                prompt_text = f.read()

        self.system_prompt: str = persona_text + "\n\n" + prompt_text

        # 메시지
        self._error_messages = [
            msg.replace("{name}", name)
            for msg in _load_lines(os.path.join(persona_dir, "messages_error.txt"))
        ] or ["오류가 발생했어요. 다시 말해주세요."]

        self._rate_limit_messages = [
            msg.replace("{name}", name)
            for msg in _load_lines(os.path.join(persona_dir, "messages_ratelimit.txt"))
        ] or ["잠시 후 다시 말해주세요."]

        self._daily_limit_messages = [
            msg.replace("{name}", name)
            for msg in _load_lines(os.path.join(persona_dir, "messages_daily_limit.txt"))
        ] or ["오늘은 쉬는 날이에요. 내일 다시 와주세요."]

    @property
    def error_message(self) -> str:
        return random.choice(self._error_messages)

    @property
    def rate_limit_message(self) -> str:
        return random.choice(self._rate_limit_messages)

    @property
    def daily_limit_message(self) -> str:
        return random.choice(self._daily_limit_messages)
