"""
감정 시스템 (메모리 기반)
- 유저별 원점수 EMA
- 시간 감쇠 (50으로 복귀)
- 공통 무드 = 활성 유저 평균
- 개인 감정 = Z-score 상대평가
"""

import math
import logging
from datetime import datetime, timezone

logger = logging.getLogger("Mood")

ALPHA = 0.05       # EMA 계수 (낮을수록 느림)
TAU = 3600         # 시간 감쇠 반감기 (초). 1시간
BASELINE = 50      # 기본값
Z_SCALE = 15       # z-score → 0-100 변환 계수

# 감정 이름 매핑
EMOTIONS = [
    (0, 5, "분노"),
    (5, 15, "짜증"),
    (15, 30, "귀찮음"),
    (30, 70, "보통"),
    (70, 85, "기분좋음"),
    (85, 95, "즐거움"),
    (95, 100, "다정함"),
]


def _emotion_name(score: float) -> str:
    score = max(0, min(100, score))
    for low, high, name in EMOTIONS:
        if low <= score < high:
            return name
    return "다정함"


class MoodSystem:
    def __init__(self):
        self._users: dict[str, dict] = {}  # user_name → {"raw": float, "updated": datetime}

    def _decay(self, raw: float, updated: datetime) -> float:
        """시간 감쇠 적용"""
        elapsed = (datetime.now(timezone.utc) - updated).total_seconds()
        return BASELINE + (raw - BASELINE) * math.exp(-elapsed / TAU)

    def _get_active(self) -> dict[str, float]:
        """활성 유저들의 감쇠 적용된 점수"""
        now = datetime.now(timezone.utc)
        active = {}
        for user, data in self._users.items():
            elapsed = (now - data["updated"]).total_seconds()
            if elapsed < TAU * 3:  # 3시간 이내만 활성
                active[user] = self._decay(data["raw"], data["updated"])
        return active

    def update(self, user_name: str, target: float):
        """AI가 set_mood 호출 시. EMA 적용."""
        target = max(0, min(100, target))
        now = datetime.now(timezone.utc)

        if user_name in self._users:
            current = self._decay(self._users[user_name]["raw"], self._users[user_name]["updated"])
        else:
            current = BASELINE

        new_raw = current * (1 - ALPHA) + target * ALPHA
        self._users[user_name] = {"raw": new_raw, "updated": now}
        logger.info(f"무드: {user_name}에 대한 감정 {current:.0f} → {new_raw:.0f} (AI 요청={target:.0f}, EMA α={ALPHA})")

    def get_global(self) -> tuple[float, str]:
        """공통 무드 (활성 유저 원점수 평균)"""
        active = self._get_active()
        if not active:
            return BASELINE, _emotion_name(BASELINE)
        avg = sum(active.values()) / len(active)
        return avg, _emotion_name(avg)

    def get_user(self, user_name: str) -> tuple[float, str]:
        """개인 감정 (Z-score 상대평가)"""
        active = self._get_active()

        if user_name not in active:
            # 신규 유저
            return BASELINE, _emotion_name(BASELINE)

        if len(active) < 2:
            # 혼자면 비교 불가
            return BASELINE, _emotion_name(BASELINE)

        scores = list(active.values())
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = math.sqrt(variance) if variance > 0 else 1

        z = (active[user_name] - mean) / std
        displayed = max(0, min(100, BASELINE + z * Z_SCALE))
        return displayed, _emotion_name(displayed)

    def get_prompt_block(self, user_name: str) -> str:
        """프롬프트용 텍스트"""
        global_score, global_emotion = self.get_global()
        user_score, user_emotion = self.get_user(user_name)

        lines = []
        lines.append(f"서버 분위기: {global_score:.0f} ({global_emotion})")
        lines.append(f"{user_name}에 대한 감정: {user_score:.0f} ({user_emotion})")
        return "## 현재 기분\n" + "\n".join(lines)
