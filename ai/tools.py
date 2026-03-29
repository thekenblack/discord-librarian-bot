"""
Gemini function calling 도구 정의 및 실행
"""

import json
from google.genai import types

from database import Database

# ── 도구 정의 ────────────────────────────────────────

library_tools = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="list_entries",
            description="도서관의 모든 엔트리(자료) 목록을 조회한다. 유저가 '뭐 있어', '목록', '자료 보여줘' 등을 요청할 때 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={},
            ),
        ),
        types.FunctionDeclaration(
            name="search_entries",
            description="키워드로 도서관 엔트리를 검색한다. 유저가 특정 주제나 제목의 자료를 찾을 때 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "keyword": types.Schema(type="STRING", description="검색 키워드"),
                },
                required=["keyword"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_entry_detail",
            description="특정 엔트리의 상세 정보와 파일 목록을 조회한다. 유저가 특정 엔트리의 파일을 보거나 다운로드하려 할 때 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "entry_id": types.Schema(type="INTEGER", description="엔트리 ID"),
                },
                required=["entry_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="save_memory",
            description="기억할 만한 정보를 저장한다. 유저의 선호, 중요한 사실, 약속 등을 기억해둘 때 사용. user_id를 넣으면 유저별 기억, 안 넣으면 공통 기억으로 저장.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "content": types.Schema(type="STRING", description="기억할 내용"),
                    "user_id": types.Schema(type="STRING", description="유저 ID (유저별 기억 시)"),
                },
                required=["content"],
            ),
        ),
        types.FunctionDeclaration(
            name="recall_memories",
            description="저장된 기억을 조회한다. user_id를 넣으면 해당 유저 기억 + 공통 기억을 함께 반환. 안 넣으면 공통 기억만 반환.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "user_id": types.Schema(type="STRING", description="유저 ID (유저별 기억 조회 시)"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="send_file",
            description="파일을 유저에게 전송한다. 유저가 파일을 달라고 하거나 다운로드를 요청할 때 사용. file_id를 모르면 먼저 search_entries나 get_entry_detail로 찾아야 한다. 막연하게 요청하면 해당 엔트리의 가장 최신 파일을 보내줘.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "file_id": types.Schema(type="INTEGER", description="전송할 파일 ID"),
                },
                required=["file_id"],
            ),
        ),
    ]),
]

# ── 도구 실행 ────────────────────────────────────────


async def execute_tool(db: Database, name: str, args: dict) -> str:
    """Gemini가 요청한 도구를 실행하고 결과를 JSON 문자열로 반환"""
    if name == "list_entries":
        books = await db.list_all_books()
        if not books:
            return json.dumps({"result": "등록된 엔트리가 없습니다."}, ensure_ascii=False)
        entries = []
        for b in books:
            entries.append({
                "id": b["id"],
                "title": b["title"],
                "author": b.get("author") or "",
                "description": b.get("description") or "",
                "file_count": b["file_count"],
            })
        return json.dumps({"entries": entries}, ensure_ascii=False)

    elif name == "search_entries":
        keyword = args.get("keyword", "")
        books = await db.search_books(keyword)
        if not books:
            return json.dumps({"result": f"'{keyword}' 검색 결과가 없습니다."}, ensure_ascii=False)
        entries = []
        for b in books:
            entries.append({
                "id": b["id"],
                "title": b["title"],
                "author": b.get("author") or "",
                "description": b.get("description") or "",
                "file_count": b["file_count"],
            })
        return json.dumps({"entries": entries}, ensure_ascii=False)

    elif name == "get_entry_detail":
        entry_id = args.get("entry_id")
        detail = await db.get_book_detail(entry_id)
        if not detail:
            return json.dumps({"result": f"ID {entry_id} 엔트리를 찾을 수 없습니다."}, ensure_ascii=False)
        files = []
        for f in detail.get("files", []):
            files.append({
                "file_id": f["id"],
                "title": f["title"],
                "filename": f["filename"],
                "file_size": f["file_size"],
                "description": f.get("description") or "",
            })
        return json.dumps({
            "id": detail["id"],
            "title": detail["title"],
            "author": detail.get("author") or "",
            "description": detail.get("description") or "",
            "files": files,
        }, ensure_ascii=False)

    elif name == "save_memory":
        content = args.get("content", "")
        user_id = args.get("user_id")
        if user_id:
            mem_id = await db.save_user_memory(user_id, content)
            return json.dumps({"result": f"유저 기억 저장 완료 (ID: {mem_id})"}, ensure_ascii=False)
        else:
            mem_id = await db.save_memory(content)
            return json.dumps({"result": f"공통 기억 저장 완료 (ID: {mem_id})"}, ensure_ascii=False)

    elif name == "recall_memories":
        user_id = args.get("user_id")
        common = await db.recall_memories(10)
        result = {"common_memories": [m["content"] for m in common]}
        if user_id:
            user_mems = await db.recall_user_memories(user_id, 10)
            result["user_memories"] = [m["content"] for m in user_mems]
        return json.dumps(result, ensure_ascii=False)

    elif name == "send_file":
        file_id = args.get("file_id")
        file_info = await db.get_file(file_id)
        if not file_info:
            return json.dumps({"result": f"파일 ID {file_id}을 찾을 수 없습니다."}, ensure_ascii=False)
        return json.dumps({
            "_action": "send_file",
            "file_id": file_info["id"],
            "title": file_info["title"],
            "filename": file_info["filename"],
            "stored_name": file_info["stored_name"],
            "file_size": file_info["file_size"],
        }, ensure_ascii=False)

    return json.dumps({"error": f"알 수 없는 도구: {name}"}, ensure_ascii=False)
