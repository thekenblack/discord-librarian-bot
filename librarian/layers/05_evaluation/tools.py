"""
Evaluator 도구 선언 (feel, memorize, forget)
"""

from google.genai import types

EVALUATOR_TOOL_NAMES = {"feel", "memorize", "forget"}

evaluator_declarations = [
    types.FunctionDeclaration(
        name="memorize",
        description="유저가 알려준 정보를 기억한다. 인물, 사실, 메모, 지식 등. 수정이 필요하면 forget 후 memorize.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "content": types.Schema(type="STRING", description="기억할 내용"),
            },
            required=["content"],
        ),
    ),
    types.FunctionDeclaration(
        name="forget",
        description="잘못된 기억을 잊는다. '잊어', '삭제해', '그거 틀려' 같은 요청에 사용.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "keyword": types.Schema(type="STRING", description="잊을 기억의 키워드"),
            },
            required=["keyword"],
        ),
    ),
    types.FunctionDeclaration(
        name="feel",
        description="감정 변화를 기록한다. 매 답변마다 호출해.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "target": types.Schema(type="STRING", description="대상 유저 ID (<@ID> 또는 숫자). 생략하면 현재 대화 상대"),
                "user_comfort": types.Schema(type="INTEGER", description="편안함 변화량 (-15 ~ +15)"),
                "user_affinity": types.Schema(type="INTEGER", description="호감도 변화량 (-15 ~ +15)"),
                "user_trust": types.Schema(type="INTEGER", description="신뢰도 변화량 (-15 ~ +15)"),
                "self_mood": types.Schema(type="INTEGER", description="기분 변화량 (-15 ~ +15)"),
                "self_energy": types.Schema(type="INTEGER", description="에너지 변화량 (-15 ~ +15)"),
                "server_vibe": types.Schema(type="INTEGER", description="분위기 변화량 (-15 ~ +15)"),
                "reason": types.Schema(type="STRING", description="사유 (20자 이내)"),
                "response": types.Schema(type="STRING", description="normal(기본 답변) 또는 ignore(무시). 생략하면 normal"),
                "reaction": types.Schema(type="STRING", description="리액션 이모지 (😊, ⚡🔥 등). 답변과 별개로 붙음. 생략 가능"),
            },
            required=["reason"],
        ),
    ),
]

evaluator_tools = [types.Tool(function_declarations=evaluator_declarations)]
