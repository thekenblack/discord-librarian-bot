import logging
from google.genai import types
from config import AI_MAX_OUTPUT_TOKENS

logger = logging.getLogger("AILibrarian")


async def run_character(self, user_id: str, user_name: str,
                         user_text: str, instruction: str) -> str:
    """Character: 단서 + 페르소나로 대사 생성. 도구 없음."""
    history = self.chat_histories.get(user_id, [])

    # 시스템 프롬프트: character + 단서
    sys_parts = []
    if self.persona.character_text:
        sys_parts.append(self.persona.character_text)
    if instruction:
        sys_parts.append(f"## 단서\n{instruction}")
    system_prompt = "\n\n".join(p for p in sys_parts if p)

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=None,  # Character는 도구 없음
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=0.8,
    )

    # 히스토리 포함 호출
    loop_contents = list(history)
    logger.info(f"[Character] API 호출 (temperature=0.8, 히스토리={len(loop_contents)}턴)")
    response = await self._call_gemini(loop_contents, config)
    reply = self._extract_reply(response)

    if reply:
        logger.info(f"[Character] 1차 응답: {reply[:100]}")

    # 반복 검사 + 리트라이
    def _needs_retry(r):
        if not r:
            return True
        is_rep = self._is_repeat(history, r)
        if is_rep:
            logger.info(f"[Character] 반복 감지: {r[:50]}")
        return is_rep

    if _needs_retry(reply):
        # 2차: 높은 temperature, 같은 지시서
        logger.warning("[Character] 2차 시도 (temperature=0.9)")
        try:
            config_2 = types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=None,
                max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                temperature=0.9,
            )
            response_2 = await self._call_gemini(loop_contents, config_2)
            r = self._extract_reply(response_2)
            logger.info(f"[Character] 2차 응답: {'빈 응답' if not r else r[:100]}")
            if r and not self._is_repeat(history, r):
                reply = r
        except Exception as e:
            logger.warning(f"[Character] 2차 실패: {e}")

    if _needs_retry(reply):
        # 3차: 페르소나만 (지시서 없이), temperature=1.0
        logger.warning("[Character] 3차 시도 (bare, temperature=1.0)")
        try:
            bare_prompt = self.persona.persona_text or ""
            config_3 = types.GenerateContentConfig(
                system_instruction=bare_prompt,
                tools=None,
                max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                temperature=1.0,
            )
            # 3차는 히스토리 없이 단발
            if user_text:
                user_content = f"{user_name}: {user_text}"
            else:
                user_content = f"({user_name}이 빈 멘션을 보냈다.)"
            bare_contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]
            response_3 = await self._call_gemini(bare_contents, config_3)
            r = self._extract_reply(response_3)
            logger.info(f"[Character] 3차 응답: {'빈 응답' if not r else r[:100]}")
            if r and not self._is_repeat(history, r):
                reply = r
        except Exception as e:
            logger.warning(f"[Character] 3차 실패: {e}")

    if _needs_retry(reply):
        logger.warning("[Character] 포기: 응답 생성 실패")
        reply = ""

    return reply
