"""
Gemini function calling 도구 정의 (L5 Evaluation)
"""

from google.genai import types
from library.db import LibraryDB
from librarian.db import LibrarianDB
import importlib as _il
_tools = _il.import_module("librarian.layers.02_execution.tools")
execute_tool = _tools.execute_tool

evaluation_declarations = [
    types.FunctionDeclaration(
        name="feel",
        description="감정 수치를 변경한다. 대화에서 감정 변화가 있을 때만.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "message_id": types.Schema(type="STRING", description="트리거 메시지 ID (중복 방지)"),
                "reason": types.Schema(type="STRING", description="변화 사유"),
                "self_mood": types.Schema(type="INTEGER", description="기분 변화량 (-15~+15)"),
                "self_energy": types.Schema(type="INTEGER", description="체력 변화량 (-15~+15)"),
                "server_vibe": types.Schema(type="INTEGER", description="서버 분위기 변화량 (-15~+15)"),
                "targets": types.Schema(type="ARRAY", items=types.Schema(
                    type="OBJECT",
                    properties={
                        "user_id": types.Schema(type="STRING"),
                        "user_name": types.Schema(type="STRING"),
                        "comfort": types.Schema(type="INTEGER"),
                        "affinity": types.Schema(type="INTEGER"),
                        "trust": types.Schema(type="INTEGER"),
                    },
                ), description="유저별 감정 변화"),
                "reaction": types.Schema(type="STRING", description="(미사용)"),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="memorize",
        description="기억을 저장한다.",
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
        description="기억을 삭제한다.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "memory_id": types.Schema(type="INTEGER", description="삭제할 기억 ID"),
            },
            required=["memory_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_summary",
        description="유저별 대화 요약을 갱신한다.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "summary": types.Schema(type="STRING", description="새 요약"),
            },
            required=["summary"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_channel_summary",
        description="채널 흐름 요약을 갱신한다.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "summary": types.Schema(type="STRING", description="새 요약"),
            },
            required=["summary"],
        ),
    ),
    types.FunctionDeclaration(
        name="memorize_alias",
        description="별명을 등록한다.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "alias": types.Schema(type="STRING", description="별명"),
                "real_name": types.Schema(type="STRING", description="실제 이름"),
            },
            required=["alias", "real_name"],
        ),
    ),
    types.FunctionDeclaration(
        name="forget_alias",
        description="별명을 삭제한다.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "alias": types.Schema(type="STRING", description="삭제할 별명"),
            },
            required=["alias"],
        ),
    ),
    types.FunctionDeclaration(
        name="update_profile",
        description="유저 프로필을 갱신한다. 인상이 바뀔 때만.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "user_id": types.Schema(type="STRING", description="유저 ID"),
                "personality": types.Schema(type="STRING"),
                "trust_evidence": types.Schema(type="STRING"),
                "preferences": types.Schema(type="STRING"),
                "risk_notes": types.Schema(type="STRING"),
                "relationship": types.Schema(type="STRING"),
            },
            required=["user_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="log_conversation",
        description="대화 품질을 기록한다. 의미 있는 대화에서만.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "channel_id": types.Schema(type="STRING"),
                "participants": types.Schema(type="STRING"),
                "quality": types.Schema(type="STRING"),
                "key_moments": types.Schema(type="STRING"),
            },
            required=["quality"],
        ),
    ),
    types.FunctionDeclaration(
        name="note_pattern",
        description="패턴을 기록한다.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "observation": types.Schema(type="STRING"),
                "scope": types.Schema(type="STRING", description="user / channel / global"),
                "target_id": types.Schema(type="STRING"),
            },
            required=["observation"],
        ),
    ),
    types.FunctionDeclaration(
        name="note_self",
        description="봇 자체 경향을 기록한다. 영구 저장.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "content": types.Schema(type="STRING"),
                "category": types.Schema(type="STRING", description="tendency / weakness / strategy"),
            },
            required=["content"],
        ),
    ),
    types.FunctionDeclaration(
        name="set_thinking",
        description="유저별 레이어 사고 수준 조정. 기본 minimal. 문제 있을 때만 올려.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "user_id": types.Schema(type="STRING"),
                "l1": types.Schema(type="STRING", description="minimal / low / medium / high"),
                "l2": types.Schema(type="STRING"),
                "l3": types.Schema(type="STRING"),
            },
            required=["user_id"],
        ),
    ),
    # ── 레이어별 피드백 (각 레이어가 직접 읽음) ──
    types.FunctionDeclaration(
        name="feedback_l1",
        description="L1(관찰자)에게 지시. 다음 턴에 L1이 직접 읽는다. scope: user(유저별), channel(채널별), global(전체).",
        parameters=types.Schema(type="OBJECT", properties={
            "scope": types.Schema(type="STRING", description="user / channel / global"),
            "scope_id": types.Schema(type="STRING", description="유저ID 또는 채널ID. global이면 생략."),
            "feedback": types.Schema(type="STRING"),
        }, required=["scope", "feedback"]),
    ),
    types.FunctionDeclaration(
        name="feedback_l2",
        description="L2(실행기)에게 지시. 다음 턴에 L2가 직접 읽는다. scope: user/channel/global.",
        parameters=types.Schema(type="OBJECT", properties={
            "scope": types.Schema(type="STRING", description="user / channel / global"),
            "scope_id": types.Schema(type="STRING"),
            "feedback": types.Schema(type="STRING"),
        }, required=["scope", "feedback"]),
    ),
    types.FunctionDeclaration(
        name="feedback_l3",
        description="L3(캐릭터)에게 지시. 다음 턴에 L3가 직접 읽는다. scope: user/channel/global.",
        parameters=types.Schema(type="OBJECT", properties={
            "scope": types.Schema(type="STRING", description="user / channel / global"),
            "scope_id": types.Schema(type="STRING"),
            "feedback": types.Schema(type="STRING"),
        }, required=["scope", "feedback"]),
    ),
    types.FunctionDeclaration(
        name="feedback_l4",
        description="L4(포매터)에게 지시. 다음 턴에 L4가 직접 읽는다. scope: user/channel/global.",
        parameters=types.Schema(type="OBJECT", properties={
            "scope": types.Schema(type="STRING", description="user / channel / global"),
            "scope_id": types.Schema(type="STRING"),
            "feedback": types.Schema(type="STRING"),
        }, required=["scope", "feedback"]),
    ),
    types.FunctionDeclaration(
        name="feedback_l5",
        description="자기 자신에게 지시. 다음 배치에서 자신이 읽는다.",
        parameters=types.Schema(type="OBJECT", properties={
            "user_id": types.Schema(type="STRING"),
            "feedback": types.Schema(type="STRING"),
        }, required=["user_id", "feedback"]),
    ),
    types.FunctionDeclaration(
        name="feedback_admin",
        description="관리자에게 보고. 시스템 이상, 반복 오류, 유저 우려, 개선 제안 등.",
        parameters=types.Schema(type="OBJECT", properties={
            "user_id": types.Schema(type="STRING"),
            "message": types.Schema(type="STRING"),
        }, required=["user_id", "message"]),
    ),
]

EVALUATION_TOOL_NAMES = {
    "feel", "memorize", "forget", "update_summary", "update_channel_summary",
    "memorize_alias", "forget_alias",
    "update_profile", "log_conversation", "note_pattern", "note_self",
    "set_thinking",
    "feedback_l1", "feedback_l2", "feedback_l3", "feedback_l4", "feedback_l5",
    "feedback_admin",
}

evaluation_tools = [types.Tool(function_declarations=evaluation_declarations)]
