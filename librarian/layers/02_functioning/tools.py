"""
Gemini function calling 도구 정의 및 실행
"""

import json
from urllib.parse import urlparse, parse_qs, urlencode
from google.genai import types

from library.db import LibraryDB
from library.cogs.shop import SHOP_ITEMS, SHOP_MAP
from librarian.db import LibrarianDB
import importlib as _il
_btc = _il.import_module("librarian.layers.02_functioning.bitcoin_data")
get_news = _btc.get_news
get_weather_for = _btc.get_weather_for

# ── URL 정규화 (core.py에서 이동) ─────────────────────

def parse_url(url: str) -> dict:
    """URL 파싱 — 카테고리, 정규화, 플랫폼별 ID 추출"""
    result = {"original_url": url, "normalized": None, "platform": None, "content_id": None}
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")

    if host in ("youtube.com", "youtu.be"):
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
        result["platform"] = "youtube_video" if content_id else "youtube"
        result["normalized"] = f"youtu.be/{content_id}" if content_id else normalize_url(url)
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

# L2 도구 이름
PROCESSOR_TOOL_NAMES = {"deliver", "attach", "gift_user", "web_search", "recognize_media", "recognize_link"}

# L2 도구 선언
functioning_declarations = [
        types.FunctionDeclaration(
            name="recognize_media",
            description="첨부된 이미지나 PDF의 내용을 확인한다. 유저가 이미지나 파일을 보내면서 물어보면 사용. 여러 개면 인덱스를 배열로.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "indices": types.Schema(type="ARRAY", items=types.Schema(type="INTEGER"), description="첨부파일 번호 배열 (0부터). 예: [0], [0, 1]"),
                },
                required=["indices"],
            ),
        ),
        types.FunctionDeclaration(
            name="recognize_link",
            description="URL의 웹페이지 내용을 확인한다. 유저가 링크를 보내면서 물어보면 사용. 여러 개면 URL을 배열로.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "urls": types.Schema(type="ARRAY", items=types.Schema(type="STRING"), description="확인할 URL 배열"),
                },
                required=["urls"],
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
            name="web_search",
            description="웹 검색. 최신 정보, 실시간 데이터, 로컬 검색에 없는 것을 찾을 때 사용.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(type="STRING", description="검색 쿼리"),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="gift_user",
            description=(
                "유저에게 선물을 준다. 고마울 때, 축하할 때, 위로할 때 등 감정적으로 주고 싶을 때 사용. "
                "사용 가능한 아이템: " + ", ".join(
                    f"{item['emoji']}{item['name']}({item['id']})" for item in SHOP_ITEMS)
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "item_id": types.Schema(type="STRING", description="아이템 ID (예: coffee, cake, book)"),
                    "message": types.Schema(type="STRING", description="선물과 함께 보내는 메시지 (캐릭터에게 전달됨)"),
                },
                required=["item_id", "message"],
            ),
        ),
]

functioning_tools = [types.Tool(function_declarations=functioning_declarations)]

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

        # 키워드 확장: 별칭
        keywords, aliases_used = await librarian_db.expand_keyword(keyword)

        # 풀 키워드로 먼저 검색
        _search_args = dict(
            exclude_memory_ids=exclude_memory_ids,
            exclude_web_ids=exclude_web_ids,
            exclude_url_ids=exclude_url_ids,
            exclude_media_ids=exclude_media_ids,
            user_name=user_name)
        result = {}
        for kw in keywords:
            kw_result = await librarian_db.search_all(kw, **_search_args)
            for cat, items in kw_result.items():
                result.setdefault(cat, []).extend(
                    item for item in items if item not in result.get(cat, []))

        # 결과 부족하면 공백 분리 서브 키워드로 보충
        if len(result) < 2 and " " in keyword:
            for part in keyword.split():
                if part in keywords:
                    continue
                part_result = await librarian_db.search_all(part, **_search_args)
                for cat, items in part_result.items():
                    result.setdefault(cat, []).extend(
                        item for item in items if item not in result.get(cat, []))
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

        # 미디어/URL 키워드 감지 → 최근 목록 반환
        _media_kw = ["이미지", "사진", "미디어", "짤", "그림", "파일", "media", "image"]
        _url_kw = ["링크", "url", "유튜브", "영상", "웹"]
        if any(mk in keyword.lower() for mk in _media_kw) and "미디어" not in result:
            _m_user, _m_other, _ = await librarian_db.get_recent_media_results(
                5, exclude_filenames=[], user_name=user_name)
            _m_rows = []
            for r in (_m_user + _m_other)[:5]:
                line = f"[media_id:{r['id']}] [{r['filename']}] {r['result'][:150]}"
                _m_rows.append(line + " (첨부 가능)")
            if _m_rows:
                result["미디어"] = _m_rows
        if any(uk in keyword.lower() for uk in _url_kw) and "유튜브" not in result and "웹" not in result:
            _u_user, _u_other, _ = await librarian_db.get_recent_url_results(
                5, user_name=user_name)
            _yt, _web = [], []
            for r in (_u_user + _u_other)[:5]:
                norm = r.get("normalized", "")
                line = f"[url_id:{r['id']}] [{r['original_url']}] {r['result'][:150]} (첨부 가능)"
                if norm.startswith("youtu.be/") or norm.startswith("youtube:"):
                    _yt.append(line)
                else:
                    _web.append(line)
            if _yt:
                result["유튜브"] = _yt
            if _web:
                result["웹"] = _web

        # 선물 기록 (항상 포함)
        _g_rows = []
        gifts = await librarian_db.get_gift_log(limit=5)
        for g in gifts:
            line = f"{g['buyer_name']}: {g['item_emoji']} {g['item_name']} ({g['item_price']} sat)"
            if g.get("message"):
                line += f' "{g["message"]}"'
            _g_rows.append(line)
        if _g_rows:
            result["선물 기록"] = _g_rows

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

    elif name == "memorize":
        content = args.get("content", "")
        author = args.get("_user_name")
        saved_id = await librarian_db.save(content, author=author)
        return json.dumps({"result": f"저장 완료 (ID: {saved_id})"}, ensure_ascii=False)

    elif name == "forget":
        keyword = args.get("keyword", "")
        count = await librarian_db.forget(keyword)
        if count > 0:
            return json.dumps({"result": f"'{keyword}' 관련 기억 {count}건 잊음"}, ensure_ascii=False)
        return json.dumps({"result": f"'{keyword}' 관련 기억 없음"}, ensure_ascii=False)

    elif name == "memorize_alias":
        aname = args.get("name", "")
        alias = args.get("alias", "")
        await librarian_db.save_alias(aname, alias)
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

    elif name == "gift_user":
        item_id = args.get("item_id", "")
        item = SHOP_MAP.get(item_id)
        if not item:
            return json.dumps({"result": f"'{item_id}' 아이템을 찾을 수 없습니다."}, ensure_ascii=False)
        # 잔고 확인
        bot_balance = args.get("_bot_balance", 0)
        if bot_balance < item["price"]:
            return json.dumps({
                "_action": "gift_failed",
                "reason": f"잔고 부족 (현재 {bot_balance} sat, 필요 {item['price']} sat)",
                "item_name": item["name"],
                "item_emoji": item["emoji"],
            }, ensure_ascii=False)
        user_id = args.get("_user_id")
        user_name = args.get("_user_name", "")
        channel_id = args.get("_channel_id")
        msg = args.get("message", "")
        return json.dumps({
            "_action": "gift_user",
            "item_id": item["id"],
            "item_name": item["name"],
            "item_emoji": item["emoji"],
            "user_id": user_id,
            "user_name": user_name,
            "channel_id": channel_id,
            "message": msg,
        }, ensure_ascii=False)

    return json.dumps({"error": f"알 수 없는 도구: {name}"}, ensure_ascii=False)
