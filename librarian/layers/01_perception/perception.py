"""
Layer 01: Perception (인식)
맥락 수집 + Gemini API 호출로 상황 분석.
결과를 Functioning과 Character에 넘긴다.
"""

import os
import logging
from datetime import datetime as dt
from google.genai import types
from config import ADMIN_IDS, LIGHTNING_ADDRESS, AI_MAX_OUTPUT_TOKENS

import importlib as _il
_btc = _il.import_module("librarian.layers.02_functioning.bitcoin_data")

logger = logging.getLogger("AILibrarian")


async def gather_context(self, user_id: str, user_name: str,
                         guild=None, reply_chain: list[str] = None,
                         pre_context: list[str] = None) -> str:
    """DB + 외부 데이터에서 raw context 수집. 순수 코드, API 호출 없음."""
    import re as _re
    import zoneinfo
    parts = []

    # 상황 정보
    try:
        tz_name = os.getenv("TZ", "Asia/Seoul")
        tz_info = zoneinfo.ZoneInfo(tz_name)
        now = dt.now(tz_info)
        utc_offset = now.strftime("%z")
        utc_str = f"UTC{utc_offset[:3]}:{utc_offset[3:]}"
    except Exception:
        now = dt.now()
        utc_str = ""
    time_str = now.strftime('%Y년 %m월 %d일 %H:%M')
    if utc_str:
        time_str += f" ({utc_str})"

    admin_names = []
    if guild:
        for aid in ADMIN_IDS:
            try:
                member = guild.get_member(int(aid))
                if member:
                    admin_names.append(member.display_name)
            except Exception:
                pass
    role = "주인 (도서관 관리자)" if user_id in ADMIN_IDS else "일반 방문자"
    situation = f"## 상황\n현재: {time_str}\n대화 상대: {user_name} ({role})"
    if admin_names:
        situation += f"\n도서관 주인: {', '.join(admin_names)}"
    if LIGHTNING_ADDRESS:
        situation += f"\n후원 라이트닝 주소: {LIGHTNING_ADDRESS}"
    parts.append(situation)

    # 비트코인 현황
    btc_block = _btc.get_prompt_block()
    if btc_block:
        parts.append(btc_block)

    # 감정 상태: 기본 상태 + 유저별 분리
    bot_emo = await self.librarian_db.get_bot_emotion()

    # 기본 상태 (봇 전체)
    bot_lines = []
    bot_lines.append(f"self_mood:{bot_emo.get('self_mood', 50):.1f}")
    bot_lines.append(f"self_capacity:{bot_emo.get('self_capacity', 50):.1f}")
    bot_lines.append(f"server_vibe:{bot_emo.get('server_vibe', 50):.1f}")

    # 유저별 상태
    user_lines = []
    user_emo = await self.librarian_db.get_user_emotion(user_id)
    if user_emo:
        user_lines.append(
            f"{user_name}: "
            + " ".join(f"{k}:{user_emo[k]:.1f}" for k in self.librarian_db.USER_AXES)
            + f" (대화 {user_emo['interaction_count']}회)")
    else:
        user_lines.append(f"{user_name}: 첫 방문 (수치 없음)")

    chain_user_ids = set()
    if reply_chain:
        for line in reply_chain:
            m = _re.search(r'<@(\d+)>', line)
            if m and m.group(1) != user_id:
                chain_user_ids.add(m.group(1))
    if chain_user_ids:
        chain_emos = await self.librarian_db.get_user_emotions_bulk(chain_user_ids)
        for uid, emo in chain_emos.items():
            name = emo.get("user_name", uid)
            user_lines.append(
                f"{name}: " + " ".join(f"{k}:{emo[k]:.1f}" for k in self.librarian_db.USER_AXES))

    emo_block = "## 감정 수치 (50이 중립, 0-100)\n"
    emo_block += "기본 상태 (봇 전체): " + " ".join(bot_lines) + "\n"
    emo_block += "유저별:\n" + "\n".join(f"  {l}" for l in user_lines)
    parts.append(emo_block)

    # 이전 피드백
    prev_feedback = await self.librarian_db.get_feedback(user_id)
    if prev_feedback:
        parts.append(f"## 이전 피드백\n{prev_feedback}")
        logger.info(f"[Perception] 이전 피드백 로드 ({len(prev_feedback)}자)")

    # 직전 대화
    if pre_context:
        parts.append("## 직전 대화\n" + "\n".join(pre_context))

    # 답글 흐름
    if reply_chain:
        parts.append("## 답글 흐름\n" + "\n".join(reply_chain))

    return "\n\n".join(parts)


async def run_perception(self, user_id: str, user_name: str,
                         user_text: str, raw_context: str) -> str:
    """raw context를 Gemini에 보내서 상황 분석. 결과를 다음 레이어에 넘긴다."""
    sys_parts = []
    if self.persona.perception_text:
        sys_parts.append(self.persona.perception_text)
    if raw_context:
        sys_parts.append(raw_context)
    system_prompt = "\n\n".join(p for p in sys_parts if p)

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=None,
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=0.3,
    )

    if user_text:
        user_content = f"{user_name}: {user_text}"
    else:
        user_content = f"({user_name}이 빈 멘션을 보냈다.)"

    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

    logger.info("[Perception] API 호출")
    response = await self._call_gemini(contents, config)
    result = self._extract_reply(response)

    if result:
        logger.info(f"[Perception] 분석 완료 ({len(result)}자): {result[:150]}")
    else:
        logger.warning("[Perception] 분석 실패 — raw context 직접 사용")
        result = raw_context

    return result
