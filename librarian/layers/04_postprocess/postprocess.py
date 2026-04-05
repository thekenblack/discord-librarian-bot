"""
Layer 04: Postprocess (디스코드 출력)
멘션 변환 + 행동 정합성 확인.
"""

import logging
from google.genai import types
from config import AI_MAX_OUTPUT_TOKENS, TEMP_L4

logger = logging.getLogger("AILibrarian")


async def run_postprocess(self, raw_reply: str, user_name: str,
                          mention_map: dict[str, str] | None = None,
                          channel_map: dict[str, str] | None = None,
                          role_map: dict[str, str] | None = None,
                          emoji_map: dict[str, str] | None = None,
                          feedback: str = "") -> str:
    """멘션/채널/역할/이모지 변환 + 시스템 텍스트 정리."""
    if not raw_reply or not raw_reply.strip():
        return ""

    sys_parts = []
    if self.persona.postprocess_text:
        sys_parts.append(self.persona.postprocess_text)
    if feedback:
        sys_parts.append(f"## 커맨드 센터 지시 (최우선)\n{feedback}")
    system_prompt = "\n\n".join(p for p in sys_parts if p)

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=None,
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=TEMP_L4,
        thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
    )

    prompt_parts = [f"대사:\n{raw_reply}"]
    if mention_map:
        lines = [f"- @{name} → <@{uid}>" for name, uid in mention_map.items()]
        prompt_parts.append("\n멘션 매핑:\n" + "\n".join(lines))
    if channel_map:
        lines = [f"- #{name} → <#{cid}>" for name, cid in channel_map.items()]
        prompt_parts.append("\n채널 매핑:\n" + "\n".join(lines))
    if role_map:
        lines = [f"- @{name} → <@&{rid}>" for name, rid in role_map.items()]
        prompt_parts.append("\n역할 매핑:\n" + "\n".join(lines))
    if emoji_map:
        lines = [f"- :{name}: → {eid}" for name, eid in emoji_map.items()]
        prompt_parts.append("\n이모지 매핑:\n" + "\n".join(lines))

    prompt = "\n".join(prompt_parts)
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

    from librarian.core import MODEL_L4
    logger.info(f"[Postprocess] API 호출 (model={MODEL_L4}, thinking=minimal)")
    response = await self._call_gemini(contents, config, model=MODEL_L4)
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
