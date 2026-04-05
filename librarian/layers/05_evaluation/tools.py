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
    types.FunctionDeclaration(
        name="memorize_alias",
        description="같은 것의 다른 이름을 등록한다. '~를 ~라고도 불러', '~는 ~의 줄임말' 같은 요청에 사용. 검색할 때 자동 확장됨.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "name": types.Schema(type="STRING", description="원래 이름"),
                "alias": types.Schema(type="STRING", description="별칭"),
            },
            required=["name", "alias"],
        ),
    ),
    types.FunctionDeclaration(
        name="forget_alias",
        description="잘못된 별칭을 삭제한다.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "alias_id": types.Schema(type="INTEGER", description="삭제할 별칭 ID"),
            },
            required=["alias_id"],
        ),
    ),
]

evaluation_declarations += [
    types.FunctionDeclaration(
        name="update_profile",
        description="유저 프로필을 갱신한다. 인상이 바뀔 때만. personality, trust_evidence, preferences, risk_notes, relationship 중 변경할 것만.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "user_id": types.Schema(type="STRING", description="유저 ID"),
                "personality": types.Schema(type="STRING", description="성격/성향"),
                "trust_evidence": types.Schema(type="STRING", description="신뢰 근거"),
                "preferences": types.Schema(type="STRING", description="선호/비선호"),
                "risk_notes": types.Schema(type="STRING", description="주의사항"),
                "relationship": types.Schema(type="STRING", description="관계 궤적"),
            },
            required=["user_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="log_conversation",
        description="대화 품질을 기록한다. 의미 있는 대화에서만. 잡담은 생략.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "channel_id": types.Schema(type="STRING", description="채널 ID"),
                "participants": types.Schema(type="STRING", description="참여자들"),
                "quality": types.Schema(type="STRING", description="레이어별 품질 평가"),
                "key_moments": types.Schema(type="STRING", description="핵심 사건"),
            },
            required=["quality"],
        ),
    ),
    types.FunctionDeclaration(
        name="note_pattern",
        description="패턴을 기록한다. 유저별, 채널별, 또는 전체 패턴.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "observation": types.Schema(type="STRING", description="관찰된 패턴"),
                "scope": types.Schema(type="STRING", description="user / channel / global"),
                "target_id": types.Schema(type="STRING", description="대상 ID (scope가 user/channel일 때)"),
            },
            required=["observation"],
        ),
    ),
    types.FunctionDeclaration(
        name="note_self",
        description="봇 자체 경향을 기록한다. 반복되는 실수, 약점, 전략.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "content": types.Schema(type="STRING", description="기록할 내용"),
                "category": types.Schema(type="STRING", description="tendency / weakness / strategy"),
            },
            required=["content"],
        ),
    ),
]

evaluation_declarations += [
    types.FunctionDeclaration(
        name="feedback_user",
        description="유저별 피드백. 이 유저와 대화할 때의 구체적 지침. 다음 턴에 L1이 읽음.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "user_id": types.Schema(type="STRING", description="유저 ID"),
                "feedback": types.Schema(type="STRING", description="구체적 지침"),
            },
            required=["user_id", "feedback"],
        ),
    ),
    types.FunctionDeclaration(
        name="feedback_channel",
        description="채널별 피드백. 이 채널에서의 행동 지침. 다음 턴에 L1이 읽음.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "channel_id": types.Schema(type="STRING", description="채널 ID"),
                "feedback": types.Schema(type="STRING", description="구체적 지침"),
            },
            required=["channel_id", "feedback"],
        ),
    ),
    types.FunctionDeclaration(
        name="feedback_global",
        description="전체 피드백. 모든 대화에 적용되는 지침. 다음 턴에 L1이 읽음.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "feedback": types.Schema(type="STRING", description="구체적 지침"),
            },
            required=["feedback"],
        ),
    ),
]

EVALUATION_TOOL_NAMES = {
    "feel", "memorize", "forget", "update_summary", "update_channel_summary",
    "memorize_alias", "forget_alias",
    "update_profile", "log_conversation", "note_pattern", "note_self",
    "feedback_user", "feedback_channel", "feedback_global",
}

evaluation_tools = [types.Tool(function_declarations=evaluation_declarations)]
