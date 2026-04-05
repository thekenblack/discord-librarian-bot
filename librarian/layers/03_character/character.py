import logging
from google.genai import types
from config import AI_MAX_OUTPUT_TOKENS, TEMP_L3

logger = logging.getLogger("AILibrarian")

character_declarations = [
    types.FunctionDeclaration(
        name="react",
        description="유저 메시지에 이모지 리액션을 단다. 자유롭게.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "emoji": types.Schema(type="STRING", description="이모지. 여러 개 가능 (예: 😊📚👋)"),
            },
            required=["emoji"],
        ),
    ),
]
character_tools = [types.Tool(function_declarations=character_declarations)]


async def run_character(self, user_id: str, user_name: str,
                         user_text: str, instruction: str,
                         context_block: str = "",
                         raw_context: str = "",
                         thinking_level: str = "minimal",
                         feedback: str = "") -> str:
    """Character: 컨텍스트 + 도구 결과 + 페르소나로 대사 생성 + 리액션."""
    history = self.chat_histories.get(user_id, [])
    sys_parts = []
    if self.persona.character_text:
        sys_parts.append(self.persona.character_text)
    if feedback:
        sys_parts.append(f"## 커맨드 센터 지시 (최우선)\n{feedback}")
    if raw_context:
        sys_parts.append(raw_context)
    if context_block:
        sys_parts.append(f"## 관찰자 분석 (Perception)\n{context_block}")
    if instruction:
        sys_parts.append(f"## 실행 보고 (Execution)\n{instruction}")
    system_prompt = "\n\n".join(p for p in sys_parts if p)
    _level_map = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=character_tools,
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=TEMP_L3,
        thinking_config=types.ThinkingConfig(thinking_level=_level_map.get(thinking_level, "MINIMAL")),
    )

    loop_contents = list(history)
    from librarian.core import MODEL_L3
    logger.info(f"[Character] API 호출 (temp={TEMP_L3}, model={MODEL_L3}, thinking={thinking_level}, 히스토리={len(loop_contents)}턴)")
    response = await self._call_gemini(loop_contents, config, model=MODEL_L3)

    reply = ""
    reactions = []
    if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if part.text and part.text.strip():
                reply = part.text.strip()
            if part.function_call and part.function_call.name == "react":
                emoji = (dict(part.function_call.args) if part.function_call.args else {}).get("emoji", "")
                if emoji:
                    reactions.append(emoji)
                    logger.info(f"[Character] 리액션: {emoji}")

    if reply:
        logger.info(f"[Character] 응답: {reply[:150]}")
    else:
        logger.info("[Character] 빈 응답 (리액션만 또는 무응답)")

    # 리액션을 _meta로 전달하기 위해 인스턴스 변수에 저장
    if reactions:
        if not hasattr(self, '_l3_reactions'):
            self._l3_reactions = []
        self._l3_reactions.extend(reactions)

    return reply
