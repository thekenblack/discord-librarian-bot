import logging
from google.genai import types
from config import AI_MAX_OUTPUT_TOKENS, TEMP_L3

logger = logging.getLogger("AILibrarian")


async def run_character(self, user_id: str, user_name: str,
                         user_text: str, instruction: str,
                         context_block: str = "",
                         raw_context: str = "") -> str:
    """Character: 컨텍스트 + 도구 결과 + 페르소나로 대사 생성. 도구 없음."""
    history = self.chat_histories.get(user_id, [])
    # 시스템 프롬프트: character + 공통 컨텍스트 + L1 분석 + L2 보고
    sys_parts = []
    if self.persona.character_text:
        sys_parts.append(self.persona.character_text)
    if raw_context:
        sys_parts.append(raw_context)
    if context_block:
        sys_parts.append(f"## 관찰자 분석 (Perception)\n{context_block}")
    if instruction:
        sys_parts.append(f"## 실행 보고 (Execution)\n{instruction}")
    system_prompt = "\n\n".join(p for p in sys_parts if p)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=None,
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=TEMP_L3,
    )

    loop_contents = list(history)
    logger.info(f"[Character] API 호출 (temp={TEMP_L3}, 히스토리={len(loop_contents)}턴)")
    response = await self._call_gemini(loop_contents, config)
    reply = self._extract_reply(response)

    if reply:
        logger.info(f"[Character] 응답: {reply[:150]}")
    else:
        logger.warning("[Character] 빈 응답")
        reply = ""

    return reply
