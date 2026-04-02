"""
Layer 04: Postprocess (후처리)
Character의 대사에서 시스템 아티팩트를 제거하고 순수 자연어만 남긴다.
Gemini API 호출.
"""

import logging
from google.genai import types
from config import AI_MAX_OUTPUT_TOKENS

logger = logging.getLogger("AILibrarian")


async def run_postprocess(self, raw_reply: str, user_name: str) -> str:
    """대사 정제. 시스템 용어 제거 + 자연어만 반환."""
    if not raw_reply or not raw_reply.strip():
        return ""

    sys_parts = []
    if self.persona.postprocess_text:
        sys_parts.append(self.persona.postprocess_text)
    system_prompt = "\n\n".join(p for p in sys_parts if p)

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=None,
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=0.1,
    )

    prompt = f"다음 대사를 검수해:\n\n{raw_reply}"
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    logger.info("[Postprocess] API 호출")
    response = await self._call_gemini(contents, config)
    result = self._extract_reply(response)

    if result and result.strip():
        changed = result.strip() != raw_reply.strip()
        if changed:
            logger.info(f"[Postprocess] 정제됨: {result[:100]}")
        else:
            logger.info("[Postprocess] 변경 없음")
        return result.strip()

    # 실패 시 원본 반환
    logger.warning("[Postprocess] 정제 실패 — 원본 사용")
    return raw_reply
