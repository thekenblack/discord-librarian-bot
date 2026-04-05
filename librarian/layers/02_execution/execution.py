import os
import json
import asyncio
import logging
import discord
from google.genai import types
from google.genai.errors import ClientError
from config import FILES_DIR, MEDIA_DIR, ADMIN_IDS, AI_MAX_OUTPUT_TOKENS, TEMP_L2
import importlib as _il
_tools = _il.import_module("librarian.layers.02_execution.tools")
execution_tools = _tools.execution_tools
execute_tool = _tools.execute_tool
normalize_url = _tools.normalize_url
parse_url = _tools.parse_url

logger = logging.getLogger("AILibrarian")


async def recognize_url_background(self, parsed: dict, user_name: str):
    """모든 URL 백그라운드 인식. 유튜브 자막 → FileData → HTML 폴백."""
    async with self._bg_semaphore:
        url = parsed["original_url"]
        normalized = parsed["normalized"]
        content_id = parsed.get("content_id")
        result = ""
        loop = asyncio.get_event_loop()

        # 1단계: 유튜브 영상 → 자막 추출
        if content_id:
            try:
                from youtube_transcript_api import YouTubeTranscriptApi
                transcript_list = await loop.run_in_executor(
                    None, lambda: YouTubeTranscriptApi.get_transcript(content_id, languages=["ko", "en"]))
                text = " ".join(t["text"] for t in transcript_list)[:8000]
                if text:
                    prompt = f"다음은 유튜브 영상 자막이야. 3-4줄로 핵심만 설명해.\n\n{text}"
                    config = types.GenerateContentConfig(max_output_tokens=500, temperature=1.0)
                    response = await self._call_gemini(
                        [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])], config)
                    result = self._extract_reply(response)
                    logger.info(f"유튜브 자막 인식 완료: {content_id}")
            except Exception as e:
                logger.info(f"유튜브 자막 없음 ({content_id}): {e}")

        # HTML 폴백 (직접 가져와서 텍스트 추출)
        if not result:
            try:
                import aiohttp
                from html.parser import HTMLParser

                class _TextExtractor(HTMLParser):
                    """HTML에서 텍스트만 추출. script/style 태그 내용 제외. og:image 추출."""
                    def __init__(self):
                        super().__init__()
                        self.parts = []
                        self._skip = False
                        self.og_image = None
                    def handle_starttag(self, tag, attrs):
                        if tag in ("script", "style", "noscript"):
                            self._skip = True
                        if tag == "meta":
                            d = dict(attrs)
                            content = d.get("content", "")
                            prop = d.get("property", d.get("name", ""))
                            if prop in ("og:description", "description", "og:title") and content:
                                self.parts.insert(0, content)
                            if prop == "og:image" and content:
                                self.og_image = content
                    def handle_endtag(self, tag):
                        if tag in ("script", "style", "noscript"):
                            self._skip = False
                    def handle_data(self, data):
                        if not self._skip:
                            t = data.strip()
                            if t:
                                self.parts.append(t)

                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                           headers={"User-Agent": "Mozilla/5.0"}) as resp:
                        if resp.status != 200:
                            raise Exception(f"HTTP {resp.status}")
                        html = await resp.text(errors="replace")

                extractor = _TextExtractor()
                extractor.feed(html[:100_000])
                text = "\n".join(extractor.parts)[:6000]

                result_parts_bg = []

                # og:image 썸네일 인식
                if extractor.og_image:
                    try:
                        og_parts = [
                            types.Part(file_data=types.FileData(file_uri=extractor.og_image)),
                            types.Part.from_text(text="이 웹페이지 프리뷰 이미지를 설명해."),
                        ]
                        og_config = types.GenerateContentConfig(max_output_tokens=300, temperature=1.0)
                        og_response = await self._call_gemini(
                            [types.Content(role="user", parts=og_parts)], og_config)
                        og_result = self._extract_reply(og_response)
                        if og_result:
                            result_parts_bg.append(f"[프리뷰] {og_result}")
                    except Exception:
                        pass

                # 텍스트 요약
                if text and len(text) > 50:
                    prompt = f"다음은 웹페이지({url})에서 추출한 텍스트야. 3-4줄로 핵심만 설명해.\n\n{text}"
                    config = types.GenerateContentConfig(max_output_tokens=500, temperature=1.0)
                    response = await self._call_gemini(
                        [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])], config)
                    text_result = self._extract_reply(response)
                    if text_result:
                        result_parts_bg.append(text_result)
                    logger.info(f"URL HTML 인식 완료: {url}")
                else:
                    logger.warning(f"URL HTML 텍스트 부족 ({url}): {len(text)}자")

                result = "\n".join(result_parts_bg) if result_parts_bg else ""
            except Exception as e:
                logger.warning(f"URL HTML 폴백 실패 ({url}): {e}")

        if result:
            await self.librarian_db.update_url_result(normalized, result, status="done")
        else:
            await self.librarian_db.update_url_result(normalized, "", status="failed")


async def recognize_file_background(self, filename: str, stored_name: str, ext: str, user_name: str):
    """TXT/EPUB/MD 파일 백그라운드 인식."""
    async with self._bg_semaphore:
        result = ""
        file_path = os.path.join(MEDIA_DIR, stored_name)
        try:
            if ext == ".epub":
                # epub: ebooklib으로 텍스트 추출
                try:
                    import ebooklib
                    from ebooklib import epub as _epub
                    loop = asyncio.get_event_loop()
                    book = await loop.run_in_executor(None, lambda: _epub.read_epub(file_path))
                    texts = []
                    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                        from html.parser import HTMLParser
                        class _Strip(HTMLParser):
                            def __init__(self):
                                super().__init__()
                                self.parts = []
                            def handle_data(self, d):
                                self.parts.append(d)
                        s = _Strip()
                        s.feed(item.get_body_content().decode("utf-8", errors="replace"))
                        texts.append(" ".join(s.parts))
                    text = "\n".join(texts)[:8000]
                except Exception:
                    # ebooklib 실패 시 바이너리로 Gemini에 전달
                    with open(file_path, "rb") as f:
                        data = f.read()
                    text = None
            elif ext in (".txt", ".md"):
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()[:8000]
            else:
                text = None

            if text:
                prompt = f"다음은 {ext} 파일({filename})의 내용이야. 3-4줄로 핵심만 설명해.\n\n{text}"
                config = types.GenerateContentConfig(max_output_tokens=500, temperature=1.0)
                response = await self._call_gemini(
                    [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])], config)
                result = self._extract_reply(response)
            elif ext == ".epub" and not text:
                # 텍스트 추출 실패 시 바이너리 직접 전달
                with open(file_path, "rb") as f:
                    data = f.read()
                file_parts = [
                    types.Part.from_bytes(data=data, mime_type="application/epub+zip"),
                    types.Part.from_text(text="3-4줄로 핵심만 설명해."),
                ]
                config = types.GenerateContentConfig(max_output_tokens=500, temperature=1.0)
                response = await self._call_gemini(
                    [types.Content(role="user", parts=file_parts)], config)
                result = self._extract_reply(response)

            if result:
                await self.librarian_db.update_media_result(filename, result)
                logger.info(f"문서 인식 완료: {filename}")
            else:
                logger.warning(f"문서 인식 실패 (빈 결과): {filename}")
        except Exception as e:
            logger.warning(f"문서 인식 실패 ({filename}): {e}")


async def run_execution(self, user_id: str, user_name: str, user_text: str,
                          attachments: list = None,
                          seen_filenames: list[str] = None,
                          perception: str = "",
                          channel_id: str = None,
                          shared_ctx: dict = None,
                          ) -> tuple[str, discord.File | None, dict]:
    """Execution: 도구 실행 + 결과 보고. (tool_results_text, files_to_send, meta) 반환."""
    import time as _time
    _meta = {"tools_called": [], "tool_results": []}

    # ── 프롬프트 조립 ──
    _tp0 = _time.monotonic()
    parts = []

    func_prompt = self.persona.execution_text or self.persona.prompt_text
    parts.append(func_prompt)

    # 공통 컨텍스트 직접 포함 (L1과 동일한 원본)
    if shared_ctx and shared_ctx.get("raw_context"):
        parts.append(shared_ctx["raw_context"])

    # L1 Perception 분석 결과 (추가 참고)
    if perception:
        parts.append(f"## 관찰자 분석 (Perception)\n{perception}")

    role = "주인 (도서관 관리자)" if user_id in ADMIN_IDS else "일반 방문자"
    logger.info(f"[Execution] 대화 상대: @{user_name} (ID: {user_id}) → {role}")

    # search 중복 제거용 ID 수집
    memory_ids = shared_ctx["memories"][1] if shared_ctx and isinstance(shared_ctx.get("memories"), tuple) else []
    _, _, web_ids = await self.librarian_db.get_recent_web_results(10, user_name=user_name)
    _, _, media_ids = await self.librarian_db.get_recent_media_results(10, exclude_filenames=seen_filenames or [], user_name=user_name)
    _, _, url_ids = await self.librarian_db.get_recent_url_results(10, user_name=user_name)


    dynamic_prompt = "\n\n".join(p for p in parts if p)
    logger.info(f"[Execution] 프롬프트: {len(dynamic_prompt)}자 ({_time.monotonic()-_tp0:.2f}s)")

    # ── 유저 메시지 ──
    if user_text:
        user_content = f"{user_name}: {user_text}"
    else:
        user_content = f"({user_name}이 빈 멘션을 보냈다.)"

    self._current_attachments = attachments or []
    files_to_send = []

    _thinking_level = (shared_ctx or {}).get("thinking", {}).get("l2", "minimal")
    _level_map = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
    config = types.GenerateContentConfig(
        system_instruction=dynamic_prompt,
        tools=[types.Tool(function_declarations=execution_tools[0].function_declarations)],
        max_output_tokens=500,
        temperature=TEMP_L2,
        thinking_config=types.ThinkingConfig(thinking_level=_level_map.get(_thinking_level, "MINIMAL")),
    )

    # Processor는 히스토리 없이 단발 호출
    loop_contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

    from librarian.core import MODEL_L2
    logger.info(f"[Execution] API 호출 (model={MODEL_L2}, thinking={_thinking_level})")
    response = await self._call_gemini(loop_contents, config, model=MODEL_L2)
    logger.info("[Execution] API 응답 수신")

    # ── 1회 응답에서 모든 function_call + 텍스트 추출 ──
    result_parts = []
    text_response = ""

    if response and response.candidates and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            # 텍스트 응답
            if part.text and part.text.strip():
                text_response = part.text.strip()

            # function_call 실행
            if not part.function_call:
                continue
            fc = part.function_call
            logger.info(f"[Execution] 도구: {fc.name}({fc.args})")
            _meta["tools_called"].append(fc.name)

            # web_search: Gemini google_search_tool 사용
            if fc.name == "web_search":
                query = (dict(fc.args) if fc.args else {}).get("query", "")
                cached = await self.librarian_db.get_web_by_query(query)
                if cached:
                    ws_text = f"웹 검색({query}): {cached['result']}"
                else:
                    ws_config = types.GenerateContentConfig(
                        tools=_tools.google_search_tool,
                        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                        temperature=1.0,
                    )
                    ws_contents = [types.Content(role="user", parts=[types.Part.from_text(text=query)])]
                    ws_result = ""
                    try:
                        ws_response = await self._call_gemini(ws_contents, ws_config)
                        ws_result = self._extract_reply(ws_response)
                    except Exception as e:
                        logger.warning(f"[Execution] 웹 검색 실패: {e}")
                    if ws_result:
                        await self.librarian_db.save_web_result(query, ws_result, user_name)
                    ws_text = f"웹 검색({query}): {ws_result or '결과 없음'}"
                result_parts.append(ws_text)
                _meta["tool_results"].append(ws_text)
                continue

            # deliver / attach / gift_user
            tool_args = dict(fc.args) if fc.args else {}
            tool_args["_user_id"] = user_id
            tool_args["_user_name"] = user_name
            tool_args["_channel_id"] = channel_id
            tool_args["_bot_balance"] = shared_ctx.get("balance", 0) if shared_ctx else 0
            tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
            tool_data = json.loads(tool_result)
            _meta["tool_results"].append(tool_result)

            if tool_data.get("_action") == "deliver":
                save_path = os.path.join(FILES_DIR, tool_data["stored_name"])
                if os.path.exists(save_path):
                    files_to_send.append(discord.File(save_path, filename=tool_data["filename"]))
                    await self.library_db.increment_download(tool_data["file_id"])
                    result_parts.append(f"[전달 성공] {tool_data['filename']}")
                else:
                    result_parts.append(f"[전달 실패] 파일을 찾을 수 없다.")

            elif tool_data.get("_action") == "attach":
                save_path = os.path.join(MEDIA_DIR, tool_data["stored_name"])
                if os.path.exists(save_path):
                    files_to_send.append(discord.File(save_path, filename=tool_data["filename"]))
                    result_parts.append(f"[첨부 성공] {tool_data['filename']}")
                else:
                    result_parts.append(f"[첨부 실패] 파일을 찾을 수 없다.")

            elif tool_data.get("_action") == "share_url":
                shared_url = tool_data.get("url", "")
                if shared_url:
                    _meta.setdefault("shared_urls", []).append(shared_url)
                result_parts.append(f"[URL 공유] {shared_url}")

            elif tool_data.get("_action") == "gift_user":
                _meta.setdefault("gifts", []).append(tool_data)
                result_parts.append(f"[선물] {tool_data['item_emoji']}{tool_data['item_name']}")

            elif tool_data.get("_action") == "gift_failed":
                result_parts.append(f"[선물 실패] {tool_data['item_emoji']}{tool_data['item_name']} — {tool_data['reason']}")

            elif tool_data.get("error"):
                result_parts.append(f"[오류] {tool_data['error']}")

            elif tool_data.get("result"):
                result_parts.append(tool_data["result"])

    # 텍스트 보고 + 도구 실행 결과를 합쳐서 반환
    parts_out = []
    if text_response:
        parts_out.append(text_response)
    if result_parts:
        parts_out.append("\n".join(result_parts))
    tool_results_text = "\n".join(parts_out)
    logger.info(f"[Execution] 보고:\n{tool_results_text}")

    return tool_results_text, files_to_send, _meta


async def build_catalog(self) -> str:
    """도서관 목록을 프롬프트용 텍스트로. 변경 시에만 다시 빌드."""
    updated_at = await self.library_db.get_catalog_updated_at()
    if updated_at == self._catalog_built_at and self._catalog_cache:
        return self._catalog_cache

    books = await self.library_db.list_all_books()
    if not books:
        self._catalog_cache = "(도서관이 비어있음)"
        self._catalog_built_at = updated_at
        return self._catalog_cache

    lines = []
    for b in books:
        detail = await self.library_db.get_book_detail(b["id"])
        alias = f" (별칭: {b['alias']})" if b.get("alias") else ""
        author = f" - {b['author']}" if b.get("author") else ""
        files = detail.get("files", [])
        if not files:
            continue
        line = f"[{b['title']}{author}]{alias}"
        if b.get("description"):
            line += f" — {b['description']}"
        for f in files:
            line += f"\n  → deliver(file_id={f['id']}) {f['filename']}"
        lines.append(line)

    self._catalog_cache = "\n".join(lines)
    self._catalog_built_at = updated_at
    return self._catalog_cache


async def build_memories(self, user_id: str, user_name: str) -> tuple[str, list[int]]:
    """기억 + 선물 기록을 프롬프트용 텍스트로 + 포함된 memory ID 반환"""
    import aiosqlite
    from config import LIBRARIAN_DB_PATH

    def _fmt(r):
        return f"- {r['author']}: {r['content'][:200]}" if r["author"] else f"- {r['content'][:200]}"

    async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, author, content FROM learned WHERE author LIKE ? AND (forgotten IS NULL OR forgotten = 0) ORDER BY id DESC LIMIT 10",
            (f"{user_name}%",))
        user_rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT id, author, content FROM learned WHERE (author IS NULL OR author NOT LIKE ?) AND (forgotten IS NULL OR forgotten = 0) ORDER BY id DESC LIMIT 10",
            (f"{user_name}%",))
        other_rows = await cursor.fetchall()

    memory_ids = [r["id"] for r in user_rows] + [r["id"] for r in other_rows]

    sections = []
    if user_rows:
        lines = [_fmt(r) for r in reversed(user_rows)]
        sections.append(f"[{user_name}의 기억]\n" + "\n".join(lines))
    if other_rows:
        lines = [_fmt(r) for r in reversed(other_rows)]
        sections.append("[최근 기억]\n" + "\n".join(lines))

    # 선물 기록 (4분류)
    def _gift_fmt(g):
        who = g.get("buyer_name") or ""
        to = g.get("recipient_name") or ""
        label = f"{who} → {to}" if to else who
        line = f"- {label}: {g['item_emoji']} {g['item_name']} ({g['item_price']} sat)"
        if g.get("message"):
            line += f' "{g["message"]}"'
        return line

    bot_id = str(self.user.id) if self.user else ""
    gift_data = await self.librarian_db.get_gifts_for_prompt(user_id, bot_id)

    if gift_data["bot_self"]:
        lines = [_gift_fmt(g) for g in reversed(gift_data["bot_self"])]
        sections.append("[내 소비 기록]\n" + "\n".join(lines))
    if gift_data["bot_to_user"]:
        lines = [_gift_fmt(g) for g in reversed(gift_data["bot_to_user"])]
        sections.append(f"[내가 {user_name}에게 준 선물]\n" + "\n".join(lines))
    if gift_data["user_to_bot"]:
        lines = [_gift_fmt(g) for g in reversed(gift_data["user_to_bot"])]
        sections.append(f"[{user_name}이 나에게 준 선물]\n" + "\n".join(lines))
    if gift_data["others"]:
        lines = [_gift_fmt(g) for g in reversed(gift_data["others"])]
        sections.append("[다른 사람과의 선물]\n" + "\n".join(lines))

    text = "\n\n".join(sections) if sections else "(기억 없음)"
    return text, memory_ids
