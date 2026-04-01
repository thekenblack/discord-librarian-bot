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

        # v5 3레이어 구조: 1_director / 2_character / 3_evaluator
        director_prompts = os.path.join(persona_dir, "1_director", "prompts")
        character_prompts = os.path.join(persona_dir, "2_character", "prompts")
        evaluator_prompts = os.path.join(persona_dir, "3_evaluator", "prompts")
        messages_dir = os.path.join(persona_dir, "messages")

        # 페르소나 프롬프트 (캐릭터)
        persona_path = os.path.join(character_prompts, "persona.txt")
        persona_text = ""
        if os.path.exists(persona_path):
            with open(persona_path, encoding="utf-8") as f:
                persona_text = f.read().replace("{name}", name)

        # 시스템 프롬프트 (동작 규칙) — Director 영역
        prompt_path = os.path.join(director_prompts, "functioning.txt")
        prompt_text = ""
        if os.path.exists(prompt_path):
            with open(prompt_path, encoding="utf-8") as f:
                prompt_text = f.read().replace("{name}", name)

        # 마무리 리마인드 프롬프트
        reminder_path = os.path.join(character_prompts, "reminder.txt")
        reminder_text = ""
        if os.path.exists(reminder_path):
            with open(reminder_path, encoding="utf-8") as f:
                reminder_text = f.read().replace("{name}", name)

        # 외양
        character_path = os.path.join(character_prompts, "character.txt")
        character_text = ""
        if os.path.exists(character_path):
            with open(character_path, encoding="utf-8") as f:
                character_text = f.read().replace("{name}", name)

        # Director / Evaluator 프롬프트 (v5)
        director_path = os.path.join(director_prompts, "director.txt")
        director_text = ""
        if os.path.exists(director_path):
            with open(director_path, encoding="utf-8") as f:
                director_text = f.read().replace("{name}", name)

        evaluator_path = os.path.join(evaluator_prompts, "evaluator.txt")
        evaluator_text = ""
        if os.path.exists(evaluator_path):
            with open(evaluator_path, encoding="utf-8") as f:
                evaluator_text = f.read().replace("{name}", name)

        self.persona_text: str = persona_text
        self.prompt_text: str = prompt_text
        self.reminder_text: str = reminder_text
        self.character_text: str = character_text
        self.director_text: str = director_text
        self.evaluator_text: str = evaluator_text

        # 에러 메시지
        self._messages = [
            msg.replace("{name}", name)
            for msg in _load_lines(os.path.join(messages_dir, "error.txt"))
        ] or ["..."]

    @property
    def error_message(self) -> str:
        return random.choice(self._messages)
