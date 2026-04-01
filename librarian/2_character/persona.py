"""
페르소나 + 시스템 프롬프트 로드 (txt 기반)
v5: 각 레이어 폴더의 prompts/ 내 모든 .txt를 파일명 순서로 합침
"""

import os
import glob
import random


def _load_lines(path: str) -> list[str]:
    """파일에서 빈 줄을 제외한 줄 목록 반환"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def _load_prompts_dir(prompts_dir: str, name: str) -> str:
    """디렉토리 내 모든 .txt를 파일명 순서로 읽어서 합침"""
    if not os.path.isdir(prompts_dir):
        return ""
    files = sorted(glob.glob(os.path.join(prompts_dir, "*.txt")))
    parts = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            text = fh.read().strip()
            if text:
                parts.append(text.replace("{name}", name))
    return "\n\n".join(parts)


class Persona:
    def __init__(self, persona_dir: str, name: str, status_text: str):
        self.name = name
        self.status_text = status_text

        # v5 3레이어 구조
        director_prompts = os.path.join(persona_dir, "1_director", "prompts")
        character_prompts = os.path.join(persona_dir, "2_character", "prompts")
        evaluator_prompts = os.path.join(persona_dir, "3_evaluator", "prompts")
        messages_dir = os.path.join(persona_dir, "messages")

        # 레이어별 프롬프트: 폴더 내 모든 .txt 합침
        self.director_text: str = _load_prompts_dir(director_prompts, name)
        self.character_text: str = _load_prompts_dir(character_prompts, name)
        self.evaluator_text: str = _load_prompts_dir(evaluator_prompts, name)

        # v4 호환 (prompt_text = director, persona_text = character 첫 파일)
        self.prompt_text: str = self.director_text
        self.persona_text: str = self.character_text
        self.reminder_text: str = ""

        # 에러 메시지
        self._messages = [
            msg.replace("{name}", name)
            for msg in _load_lines(os.path.join(messages_dir, "error.txt"))
        ] or ["..."]

    @property
    def error_message(self) -> str:
        return random.choice(self._messages)
