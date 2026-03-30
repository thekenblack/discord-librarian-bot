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

        prompts_dir = os.path.join(persona_dir, "prompts")
        messages_dir = os.path.join(persona_dir, "messages")

        # 페르소나 프롬프트 (캐릭터)
        persona_path = os.path.join(prompts_dir, "persona.txt")
        persona_text = ""
        if os.path.exists(persona_path):
            with open(persona_path, encoding="utf-8") as f:
                persona_text = f.read().replace("{name}", name)

        # 시스템 프롬프트 (동작 규칙)
        prompt_path = os.path.join(prompts_dir, "functioning.txt")
        prompt_text = ""
        if os.path.exists(prompt_path):
            with open(prompt_path, encoding="utf-8") as f:
                prompt_text = f.read().replace("{name}", name)

        # 마무리 리마인드 프롬프트
        reminder_path = os.path.join(prompts_dir, "reminder.txt")
        reminder_text = ""
        if os.path.exists(reminder_path):
            with open(reminder_path, encoding="utf-8") as f:
                reminder_text = f.read().replace("{name}", name)

        self.persona_text: str = persona_text
        self.prompt_text: str = prompt_text
        self.reminder_text: str = reminder_text

        # 에러 메시지
        self._messages = [
            msg.replace("{name}", name)
            for msg in _load_lines(os.path.join(messages_dir, "error.txt"))
        ] or ["⚡ 미안, 나중에 다시 불러줘"]

    @property
    def error_message(self) -> str:
        return random.choice(self._messages)
