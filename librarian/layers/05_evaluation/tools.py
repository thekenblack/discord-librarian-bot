"""
Evaluation 도구 선언 (feel, memorize, forget)
"""

from google.genai import types

EVALUATION_TOOL_NAMES = {"feel", "memorize", "forget", "update_summary", "update_channel_summary"}

evaluation_declarations = [
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
        description="감정 변화를 기록한다. 1회 호출로 여러 유저를 동시에 처리할 수 있다. targets에 유저별 변화량을 지정하고, 봇 전체 축은 별도로 지정.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "targets": types.Schema(
                    type="ARRAY",
                    description="유저별 감정 변화 배열. 생략하면 현재 대화 상대 1명.",
                    items=types.Schema(
                        type="OBJECT",
                        properties={
                            "user_id": types.Schema(type="STRING", description="유저 ID (<@ID> 또는 숫자)"),
                            "comfort": types.Schema(type="INTEGER", description="편안함 변화량 (-15 ~ +15)"),
                            "affinity": types.Schema(type="INTEGER", description="호감도 변화량 (-15 ~ +15)"),
                            "trust": types.Schema(type="INTEGER", description="신뢰도 변화량 (-15 ~ +15)"),
                        },
                    ),
                ),
                "self_mood": types.Schema(type="INTEGER", description="기분 변화량 (-15 ~ +15)"),
                "self_energy": types.Schema(type="INTEGER", description="에너지 변화량 (-15 ~ +15)"),
                "server_vibe": types.Schema(type="INTEGER", description="분위기 변화량 (-15 ~ +15)"),
                "message_id": types.Schema(type="STRING", description="감정 변화의 원인이 된 메시지 ID. 중복 방지용."),
                "reason": types.Schema(type="STRING", description="사유 (20자 이내)"),
                "response": types.Schema(type="STRING", description="normal(기본 답변) 또는 ignore(무시). 생략하면 normal"),
                "reaction": types.Schema(type="STRING", description="리액션 이모지 (😊, ⚡🔥 등). 답변과 별개로 붙음. 생략 가능"),
            },
            required=["reason", "message_id"],
        ),
    ),
]

evaluation_declarations += [
    types.FunctionDeclaration(
        name="update_summary",
        description="유저와의 대화 요약을 갱신한다. 이전 요약에 이번 대화를 반영해서 덮어쓴다. 대화 톤, 주요 주제, 관계 흐름을 포함.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "summary": types.Schema(type="STRING", description="갱신된 유저 대화 요약 (200자 이내)"),
            },
            required=["summary"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_channel_summary",
        description="채널 흐름 요약을 갱신한다. 이 채널에서 최근 벌어지고 있는 대화 흐름, 참여자, 주제를 요약.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "summary": types.Schema(type="STRING", description="갱신된 채널 흐름 요약 (200자 이내)"),
            },
            required=["summary"],
        ),
    ),
]

evaluation_tools = [types.Tool(function_declarations=evaluation_declarations)]
