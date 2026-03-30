"""
Gemini function calling 도구 정의 및 실행
"""

import json
from google.genai import types

from library.db import LibraryDB
from librarian.db import LibrarianDB

# ── 도구 정의 ────────────────────────────────────────

google_search_tool = [types.Tool(google_search=types.GoogleSearch())]

library_tools = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="search",
            description="지식과 기억을 검색한다. 프롬프트에 없는 정보가 필요할 때 사용.",
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
        types.FunctionDeclaration(
            name="web_search",
            description="웹 검색이 필요할 때 호출. 최신 정보, 실시간 데이터, 내 지식에 없는 것을 찾을 때 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(type="STRING", description="검색할 내용"),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="add_alias",
            description="별칭을 등록한다. '~를 ~라고도 불러', '~의 별칭은 ~' 같은 말을 할 때 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "name": types.Schema(type="STRING", description="원래 이름"),
                    "alias": types.Schema(type="STRING", description="별칭"),
                },
                required=["name", "alias"],
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
        result = {}

        # 키워드 확장: 별칭 + 공백 분리
        keywords = await librarian_db.expand_keyword(keyword)
        if " " in keyword:
            for part in keyword.split():
                if part not in keywords:
                    keywords.append(part)

        # 지식 + 기억 검색 (도서는 프롬프트에 이미 있으므로 제외)
        seen_content = set()
        for kw in keywords:
            kw_result = await librarian_db.search_all(kw)
            for key, items in kw_result.items():
                for item in items:
                    if item not in seen_content:
                        seen_content.add(item)
                        result.setdefault(key, []).append(item)
        for key in result:
            result[key] = result[key][:5]

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

    elif name == "add_alias":
        aname = args.get("name", "")
        alias = args.get("alias", "")
        await librarian_db.add_alias(aname, alias)
        return json.dumps({"result": f"별칭 등록: {aname} = {alias}"}, ensure_ascii=False)

    return json.dumps({"error": f"알 수 없는 도구: {name}"}, ensure_ascii=False)
