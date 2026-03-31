"""
Gemini function calling 도구 정의 및 실행
"""

import json
from urllib.parse import urlparse, parse_qs, urlencode
from google.genai import types

from library.db import LibraryDB
from librarian.db import LibrarianDB
from librarian.bitcoin_data import get_news, get_weather_for

# ── URL 정규화 (core.py에서 이동) ─────────────────────

def parse_url(url: str) -> dict:
    """URL 파싱 — 카테고리, 정규화, 플랫폼별 ID 추출"""
    result = {"original_url": url, "normalized": None, "platform": None, "content_id": None}
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")

    if host in ("youtube.com", "youtu.be"):
        result["platform"] = "youtube"
        if host == "youtu.be":
            content_id = parsed.path.lstrip("/").split("/")[0]
        else:
            path = parsed.path.rstrip("/")
            if path.startswith("/watch"):
                content_id = parse_qs(parsed.query).get("v", [None])[0]
            elif path.startswith(("/shorts/", "/live/")):
                content_id = path.split("/")[2] if len(path.split("/")) > 2 else None
            else:
                content_id = None
        result["content_id"] = content_id
        result["normalized"] = f"youtube:{content_id}" if content_id else normalize_url(url)
    else:
        result["normalized"] = normalize_url(url)

    return result


def normalize_url(url: str) -> str:
    """URL 정규화"""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    host = host.removeprefix("www.").removeprefix("m.")
    path = parsed.path.rstrip("/")
    if path.endswith(("/index.html", "/index.htm")):
        path = path.rsplit("/", 1)[0]
    _tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                  "fbclid", "gclid", "si", "ref", "source", "feature"}
    params = {k: v for k, v in parse_qs(parsed.query).items() if k not in _tracking}
    query = urlencode(params, doseq=True) if params else ""
    result = host + path
    if query:
        result += "?" + query
    return result.lower()

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
        types.FunctionDeclaration(
            name="recognize_link",
            description="URL의 웹페이지 내용을 확인한다. 유저가 링크를 보내면서 '이거 뭐야', '요약해줘' 같은 요청을 하면 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "url": types.Schema(type="STRING", description="확인할 URL"),
                },
                required=["url"],
            ),
        ),
        types.FunctionDeclaration(
            name="attach",
            description="이전에 인식한 미디어 또는 URL을 공유한다. search 결과의 media_id 또는 url_id를 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "media_id": types.Schema(type="INTEGER", description="미디어 ID"),
                    "url_id": types.Schema(type="INTEGER", description="URL ID"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="feel",
            description="감정 변화를 기록한다. 매 답변마다 호출해.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "target": types.Schema(type="STRING", description="대상 유저 ID (<@ID> 또는 숫자). 생략하면 현재 대화 상대"),
                    "user_friendly": types.Schema(type="INTEGER", description="호감/우정 변화량 (-3, -2, -1, 0, +1, +2, +3)"),
                    "user_lovely": types.Schema(type="INTEGER", description="애정/설렘 변화량 (-3, -2, -1, 0, +1, +2, +3)"),
                    "user_trust": types.Schema(type="INTEGER", description="신뢰 변화량 (-3, -2, -1, 0, +1, +2, +3)"),
                    "self_mood": types.Schema(type="INTEGER", description="내 기분 변화량 (-3, -2, -1, 0, +1, +2, +3)"),
                    "self_energy": types.Schema(type="INTEGER", description="기력 변화량 (-3, -2, -1, 0, +1, +2, +3)"),
                    "server_vibe": types.Schema(type="INTEGER", description="서버 분위기 변화량 (-3, -2, -1, 0, +1, +2, +3)"),
                    "reason": types.Schema(type="STRING", description="사유 (20자 이내)"),
                    "response": types.Schema(type="STRING", description="ignore(무시), 이모지(😊 등), 또는 생략(기본 답변)"),
                },
                required=["reason"],
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
        exclude_url_ids = args.get("_exclude_url_ids", [])
        exclude_media_ids = args.get("_exclude_media_ids", [])

        # 키워드 확장: 별칭 + 공백 분리
        keywords, aliases_used = await librarian_db.expand_keyword(keyword)
        if " " in keyword:
            for part in keyword.split():
                if part not in keywords:
                    keywords.append(part)

        # 7개 카테고리 검색
        merged = {}
        for kw in keywords:
            kw_result = await librarian_db.search_all(
                kw, exclude_memory_ids=exclude_memory_ids,
                exclude_web_ids=exclude_web_ids,
                exclude_url_ids=exclude_url_ids,
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
            if news["domestic"]:
                result["뉴스_국내"] = news["domestic"]
            if news["international"]:
                result["뉴스_국제"] = news["international"]

        # 날씨 키워드 감지
        weather_keywords = ["날씨", "기온", "weather"]
        if any(wk in keyword.lower() for wk in weather_keywords):
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

    elif name == "attach":
        media_id = args.get("media_id")
        url_id = args.get("url_id")
        if media_id:
            media = await librarian_db.get_media_by_id(media_id)
            if not media:
                return json.dumps({"result": f"미디어 ID {media_id}을 찾을 수 없습니다."}, ensure_ascii=False)
            if not media.get("stored_name"):
                return json.dumps({"result": f"미디어 ID {media_id}의 파일이 저장되어 있지 않습니다."}, ensure_ascii=False)
            return json.dumps({
                "_action": "attach",
                "media_id": media["id"],
                "filename": media["filename"],
                "stored_name": media["stored_name"],
                "description": media["result"][:200],
            }, ensure_ascii=False)
        if url_id:
            url = await librarian_db.get_url_by_id(url_id)
            if not url:
                return json.dumps({"result": f"URL ID {url_id}을 찾을 수 없습니다."}, ensure_ascii=False)
            return json.dumps({
                "_action": "share_url",
                "url_id": url["id"],
                "url": url.get("original_url", ""),
                "description": url["result"][:200],
            }, ensure_ascii=False)
        return json.dumps({"result": "media_id 또는 url_id를 지정해주세요."}, ensure_ascii=False)

    return json.dumps({"error": f"알 수 없는 도구: {name}"}, ensure_ascii=False)
