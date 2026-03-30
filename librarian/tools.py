"""
Gemini function calling 도구 정의 및 실행
"""

import json
from google.genai import types

from library.db import LibraryDB
from librarian.db import LibrarianDB
from librarian.bitcoin_data import get_news, get_weather_for

# ── 도구 정의 ────────────────────────────────────────

google_search_tool = [types.Tool(google_search=types.GoogleSearch())]

library_tools = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="search",
            description="비트코인/경제/철학 지식과 유저 기억을 검색한다. 질문이 오면 먼저 이걸로 확인해. 뉴스 헤드라인이나 도시 날씨도 검색 가능.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "keyword": types.Schema(type="STRING", description="검색 키워드"),
                },
                required=["keyword"],
            ),
        ),
        types.FunctionDeclaration(
            name="deliver",
            description="책이나 자료를 갖다준다. 유저가 '줘', '보내줘', '갖다줘', '가져와' 등 요청하면 호출해. 도서관 목록의 file ID를 써.",
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
            description="유저가 알려준 정보를 기억한다. 인물, 사실, 메모 등.",
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
            description="잘못된 별칭을 삭제한다. search 결과의 aliases에 있는 id를 써.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "alias_id": types.Schema(type="INTEGER", description="삭제할 별칭 ID (search 결과에서 확인)"),
                },
                required=["alias_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="forget_memory",
            description="잘못된 기억이나 더 이상 필요 없는 기억을 잊는다. '잊어', '삭제해', '그거 틀려' 같은 요청에 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "keyword": types.Schema(type="STRING", description="잊을 기억의 키워드"),
                },
                required=["keyword"],
            ),
        ),
        types.FunctionDeclaration(
            name="modify_memory",
            description="기존 기억을 수정한다. 기존 것을 잊고 새 내용으로 대체. '아니야 ~야', '그거 틀려 ~가 맞아' 같은 요청에 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "keyword": types.Schema(type="STRING", description="잊을 기존 기억의 키워드"),
                    "new_content": types.Schema(type="STRING", description="새로 기억할 내용"),
                },
                required=["keyword", "new_content"],
            ),
        ),
        types.FunctionDeclaration(
            name="recognize_media",
            description="첨부된 이미지나 PDF의 내용을 확인한다. 유저가 이미지나 파일을 보내면서 '이거 뭐야', '읽어봐' 같은 요청을 하면 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "attachment_index": types.Schema(type="INTEGER", description="첨부파일 번호 (0부터)"),
                },
                required=["attachment_index"],
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
        user_name = args.get("_user_name")
        exclude_memory_ids = args.get("_exclude_memory_ids", [])
        exclude_web_ids = args.get("_exclude_web_ids", [])
        exclude_media_ids = args.get("_exclude_media_ids", [])

        # 키워드 확장: 별칭 + 공백 분리
        keywords, aliases_used = await librarian_db.expand_keyword(keyword)
        if " " in keyword:
            for part in keyword.split():
                if part not in keywords:
                    keywords.append(part)

        # 5개 카테고리 검색
        merged = {}
        for kw in keywords:
            kw_result = await librarian_db.search_all(
                kw, exclude_memory_ids=exclude_memory_ids,
                exclude_web_ids=exclude_web_ids,
                exclude_media_ids=exclude_media_ids,
                user_name=user_name)
            for cat, items in kw_result.items():
                for item in items:
                    if item not in merged.setdefault(cat, set()):
                        merged.setdefault(cat, set()).add(item)

        result = {}
        for cat, items in merged.items():
            result[cat] = list(items)[:10]
        # 뉴스 키워드 감지
        if "뉴스" in keyword or "news" in keyword.lower() or "헤드라인" in keyword:
            news = get_news()
            if news["domestic"] or news["international"]:
                result["news"] = news

        # 날씨 키워드 감지
        weather_keywords = ["날씨", "기온", "weather"]
        if any(wk in keyword.lower() for wk in weather_keywords):
            # 키워드에서 날씨 관련 단어 제거하고 도시명 추출
            city = keyword
            for wk in weather_keywords:
                city = city.replace(wk, "").strip()
            if city:
                weather = await get_weather_for(city)
                if weather:
                    result["weather"] = weather

        if not result:
            result["info"] = f"'{keyword}'에 대해 아는 게 없음."
        if aliases_used:
            result["aliases"] = aliases_used
        return json.dumps(result, ensure_ascii=False)

    elif name == "deliver":
        file_id = args.get("file_id")
        file_info = await library_db.get_file(file_id)
        if not file_info:
            return json.dumps({"result": f"파일 ID {file_id}을 찾을 수 없습니다."}, ensure_ascii=False)
        return json.dumps({
            "_action": "deliver",
            "file_id": file_info["id"], "title": file_info["title"],
            "filename": file_info["filename"], "stored_name": file_info["stored_name"],
            "file_size": file_info["file_size"],
        }, ensure_ascii=False)

    elif name == "save_memory" or name == "add_knowledge":
        content = args.get("content", "")
        author = args.get("_user_name")
        saved_id = await librarian_db.save(content, author=author)
        return json.dumps({"result": f"저장 완료 (ID: {saved_id})"}, ensure_ascii=False)

    elif name == "forget_memory":
        keyword = args.get("keyword", "")
        count = await librarian_db.forget(keyword)
        if count > 0:
            return json.dumps({"result": f"'{keyword}' 관련 기억 {count}건 잊음"}, ensure_ascii=False)
        return json.dumps({"result": f"'{keyword}' 관련 기억 없음"}, ensure_ascii=False)

    elif name == "modify_memory":
        keyword = args.get("keyword", "")
        new_content = args.get("new_content", "")
        author = args.get("_user_name")
        forgotten = await librarian_db.forget(keyword)
        saved_id = await librarian_db.save(new_content, author=author)
        return json.dumps({"result": f"기억 수정: {forgotten}건 잊고 새로 저장 (ID: {saved_id})"}, ensure_ascii=False)

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

    elif name == "forget_alias":
        alias_id = args.get("alias_id")
        deleted = await librarian_db.delete_alias(alias_id)
        if deleted:
            return json.dumps({"result": f"별칭 ID {alias_id} 삭제 완료"}, ensure_ascii=False)
        return json.dumps({"result": f"별칭 ID {alias_id}을 찾을 수 없음"}, ensure_ascii=False)

    return json.dumps({"error": f"알 수 없는 도구: {name}"}, ensure_ascii=False)
