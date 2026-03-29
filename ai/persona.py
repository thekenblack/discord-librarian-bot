"""
페르소나 로드
"""

import json
import random


class Persona:
    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        self.name: str = data["name"]
        self.system_prompt: str = data["system_prompt"].replace("{name}", self.name)
        self.status_text: str = data.get("status_text", self.name)

        self._error_messages: list[str] = [
            msg.replace("{name}", self.name)
            for msg in data.get("error_messages", ["오류가 발생했어요. 다시 말해주세요."])
        ]
        self._rate_limit_messages: list[str] = [
            msg.replace("{name}", self.name)
            for msg in data.get("rate_limit_messages", ["잠시 후 다시 말해주세요."])
        ]

    @property
    def error_message(self) -> str:
        return random.choice(self._error_messages)

    @property
    def rate_limit_message(self) -> str:
        return random.choice(self._rate_limit_messages)
