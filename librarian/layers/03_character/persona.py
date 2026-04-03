"""
페르소나 + 시스템 프롬프트 로드 (txt 기반)
v5: 5레이어 구조. 각 레이어 폴더의 prompts/ 내 모든 .txt를 파일명 순서로 합침.
"""

import os
import glob
import random
from config import AI_CREATOR, AI_COMMUNITY, AI_COMMUNITY_DESC


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
                text = text.replace("{name}", name)
                text = text.replace("{creator}", AI_CREATOR)
                text = text.replace("{community}", AI_COMMUNITY)
                text = text.replace("{community_desc}", AI_COMMUNITY_DESC)
                parts.append(text)
    return "\n\n".join(parts)


class Persona:
    def __init__(self, persona_dir: str, name: str, status_text: str):
        self.name = name
        self.status_text = status_text

        layers_dir = os.path.join(persona_dir, "layers")
        messages_dir = os.path.join(persona_dir, "messages")

        # 5레이어 프롬프트
        self.perception_text: str = _load_prompts_dir(
            os.path.join(layers_dir, "01_perception", "prompts"), name)
        self.functioning_text: str = _load_prompts_dir(
            os.path.join(layers_dir, "02_functioning", "prompts"), name)
        self.character_text: str = _load_prompts_dir(
            os.path.join(layers_dir, "03_character", "prompts"), name)
        self.postprocess_text: str = _load_prompts_dir(
            os.path.join(layers_dir, "04_postprocess", "prompts"), name)
        self.evaluation_text: str = _load_prompts_dir(
            os.path.join(layers_dir, "05_evaluation", "prompts"), name)

        # 하위 호환
        self.processor_text: str = self.functioning_text

        self.prompt_text: str = self.functioning_text
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
