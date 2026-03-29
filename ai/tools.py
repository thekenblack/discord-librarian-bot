"""
Gemini function calling 도구 정의 및 실행
"""

import json
from google.genai import types

from library_db import LibraryDB
from librarian_db import LibrarianDB

# ── 도구 정의 ────────────────────────────────────────

library_tools = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="search",
            description="도서관, 지식, 유저 기억을 한번에 검색한다. 질문이나 요청이 오면 이 도구를 먼저 호출해. 도서/자료/인물/개념/잡담 뭐든 이걸로 검색.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "keyword": types.Schema(type="STRING", description="검색 키워드"),
                },
                required=["keyword"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_entries",
            description="도서관의 모든 엔트리(자료) 목록을 조회한다.",
            parameters=types.Schema(
                type="OBJECT",
                properties={},
            ),
        ),
        types.FunctionDeclaration(
            name="get_entry_detail",
            description="특정 엔트리의 상세 정보와 파일 목록을 조회한다.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "entry_id": types.Schema(type="INTEGER", description="엔트리 ID"),
                },
                required=["entry_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="send_file",
            description="파일을 유저에게 전송한다. file_id를 모르면 먼저 search로 찾아.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "file_id": types.Schema(type="INTEGER", description="전송할 파일 ID"),
                },
                required=["file_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="save_memory",
            description="기억할 만한 정보를 저장한다.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "content": types.Schema(type="STRING", description="기억할 내용"),
                },
                required=["content"],
            ),
        ),
        types.FunctionDeclaration(
            name="add_knowledge",
            description="새로운 지식을 저장한다.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "content": types.Schema(type="STRING", description="저장할 지식 내용"),
                },
                required=["content"],
            ),
        ),
        types.FunctionDeclaration(
            name="add_entry_alias",
            description="도서관 엔트리에 별칭을 추가한다.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "entry_id": types.Schema(type="INTEGER", description="엔트리 ID"),
                    "alias": types.Schema(type="STRING", description="추가할 별칭"),
                },
                required=["entry_id", "alias"],
            ),
        ),
    ]),
]

# ── 도구 실행 ────────────────────────────────────────


async def execute_tool(library_db: LibraryDB, librarian_db: LibrarianDB,
                       name: str, args: dict) -> str:
    """Gemini가 요청한 도구를 실행하고 결과를 JSON 문자열로 반환"""

    if name == "search":
        keyword = args.get("keyword", "")
        user_id = args.get("_user_id", "")
        result = {}

        # 도서 검색
        books = await library_db.search_books(keyword)
        if books:
            result["도서"] = [{"id": b["id"], "title": b["title"],
                             "author": b.get("author") or "", "file_count": b["file_count"]}
                            for b in books[:3]]

        # 지식 + 기억 통합 검색
        knowledge = await librarian_db.search_all(keyword)
        result.update(knowledge)

        if not result:
            return json.dumps({"result": f"'{keyword}' 관련 정보 없음."}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)

    elif name == "list_entries":
        books = await library_db.list_all_books()
        if not books:
            return json.dumps({"result": "등록된 엔트리가 없습니다."}, ensure_ascii=False)
        entries = [{"id": b["id"], "title": b["title"],
                   "author": b.get("author") or "", "file_count": b["file_count"]}
                  for b in books]
        return json.dumps({"entries": entries}, ensure_ascii=False)

    elif name == "get_entry_detail":
        entry_id = args.get("entry_id")
        detail = await library_db.get_book_detail(entry_id)
        if not detail:
            return json.dumps({"result": f"ID {entry_id} 엔트리를 찾을 수 없습니다."}, ensure_ascii=False)
        files = [{"file_id": f["id"], "title": f["title"], "filename": f["filename"],
                  "file_size": f["file_size"]} for f in detail.get("files", [])]
        return json.dumps({"id": detail["id"], "title": detail["title"],
                          "author": detail.get("author") or "", "files": files}, ensure_ascii=False)

    elif name == "send_file":
        file_id = args.get("file_id")
        file_info = await library_db.get_file(file_id)
        if not file_info:
            return json.dumps({"result": f"파일 ID {file_id}을 찾을 수 없습니다."}, ensure_ascii=False)
        return json.dumps({
            "_action": "send_file",
            "file_id": file_info["id"], "title": file_info["title"],
            "filename": file_info["filename"], "stored_name": file_info["stored_name"],
            "file_size": file_info["file_size"],
        }, ensure_ascii=False)

    elif name == "save_memory" or name == "add_knowledge":
        content = args.get("content", "")
        saved_id = await librarian_db.save(content)
        return json.dumps({"result": f"저장 완료 (ID: {saved_id})"}, ensure_ascii=False)

    elif name == "add_entry_alias":
        entry_id = args.get("entry_id")
        alias = args.get("alias", "")
        book = await library_db.get_book(entry_id)
        if not book:
            return json.dumps({"result": f"ID {entry_id} 엔트리를 찾을 수 없습니다."}, ensure_ascii=False)
        existing = book.get("alias") or ""
        new_alias = f"{existing}, {alias}" if existing else alias
        await library_db.update_book_alias(entry_id, new_alias)
        return json.dumps({"result": f"'{book['title']}' 엔트리에 별칭 '{alias}' 추가 완료"}, ensure_ascii=False)

    return json.dumps({"error": f"알 수 없는 도구: {name}"}, ensure_ascii=False)
