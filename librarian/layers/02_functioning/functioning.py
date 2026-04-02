import os
import json
import asyncio
import logging
import discord
from google.genai import types
from google.genai.errors import ClientError
from config import FILES_DIR, MEDIA_DIR, ADMIN_IDS, AI_MAX_OUTPUT_TOKENS
import importlib as _il
_tools = _il.import_module("librarian.layers.02_functioning.tools")
functioning_tools = _tools.functioning_tools
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
                    config = types.GenerateContentConfig(max_output_tokens=500, temperature=0.3)
                    response = await self._call_gemini(
                        [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])], config)
                    result = self._extract_reply(response)
                    logger.info(f"유튜브 자막 인식 완료: {content_id}")
            except Exception as e:
                logger.info(f"유튜브 자막 없음 ({content_id}): {e}")

        # 2단계: FileData (이미지/PDF 등 직접 전달 가능한 URL)
        if not result:
            try:
                link_parts = [
                    types.Part(file_data=types.FileData(file_uri=url)),
                    types.Part.from_text(text="3-4줄로 핵심만 설명해."),
                ]
                config = types.GenerateContentConfig(max_output_tokens=500, temperature=0.5)
                response = await self._call_gemini(
                    [types.Content(role="user", parts=link_parts)], config)
                result = self._extract_reply(response)
                logger.info(f"URL FileData 인식 완료: {url}")
            except Exception as e:
                logger.warning(f"URL FileData 인식 실패 ({url}): {e}")

        # 3단계: HTML 폴백 (FileData 실패 시 직접 가져와서 텍스트 추출)
        if not result:
            try:
                import aiohttp
                from html.parser import HTMLParser

                class _TextExtractor(HTMLParser):
                    """HTML에서 텍스트만 추출. script/style 태그 내용 제외."""
                    def __init__(self):
                        super().__init__()
                        self.parts = []
                        self._skip = False
                    def handle_starttag(self, tag, attrs):
                        if tag in ("script", "style", "noscript"):
                            self._skip = True
                        # og:description 등 메타태그 추출
                        if tag == "meta":
                            d = dict(attrs)
                            content = d.get("content", "")
                            prop = d.get("property", d.get("name", ""))
                            if prop in ("og:description", "description", "og:title") and content:
                                self.parts.insert(0, content)
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
                if text and len(text) > 50:
                    prompt = f"다음은 웹페이지({url})에서 추출한 텍스트야. 3-4줄로 핵심만 설명해.\n\n{text}"
                    config = types.GenerateContentConfig(max_output_tokens=500, temperature=0.5)
                    response = await self._call_gemini(
                        [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])], config)
                    result = self._extract_reply(response)
                    logger.info(f"URL HTML 폴백 인식 완료: {url}")
                else:
                    logger.warning(f"URL HTML 텍스트 부족 ({url}): {len(text)}자")
            except Exception as e:
                logger.warning(f"URL HTML 폴백 실패 ({url}): {e}")

        if result:
            await self.librarian_db.update_url_result(normalized, result, status="done")
        else:
            await self.librarian_db.update_url_result(normalized, "", status="failed")


async def run_functioning(self, user_id: str, user_name: str, user_text: str,
                          catalog: str, memories_text: str,
                          memory_ids: list[int] = None,
                          attachments: list = None,
                          seen_filenames: list[str] = None,
                          perception: str = "",
                          ) -> tuple[str, discord.File | None, dict]:
    """Functioning: 도구 실행 + 결과 보고. (tool_results_text, file_to_send, meta) 반환."""
    import time as _time
    _meta = {"tools_called": [], "tool_results": []}

    # ── 프롬프트 조립 ──
    _tp0 = _time.monotonic()
    parts = []

    func_base = self.persona.functioning_text or self.persona.prompt_text
    func_prompt = func_base.replace("{library_catalog}", catalog).replace("{learned_memories}", memories_text)
    parts.append(func_prompt)

    # L1 Perception 분석 결과 포함
    if perception:
        parts.append(f"## 상황 분석 (Perception)\n{perception}")

    role = "주인 (도서관 관리자)" if user_id in ADMIN_IDS else "일반 방문자"
    logger.info(f"[Functioning] 대화 상대: {user_name} (ID: {user_id}) → {role}")

    # search 중복 제거용 ID 수집
    memory_ids = memory_ids or []
    _, _, web_ids = await self.librarian_db.get_recent_web_results(10, user_name=user_name)
    _, _, media_ids = await self.librarian_db.get_recent_media_results(10, exclude_filenames=seen_filenames or [], user_name=user_name)
    _, _, url_ids = await self.librarian_db.get_recent_url_results(10, user_name=user_name)

    dynamic_prompt = "\n\n".join(p for p in parts if p)
    logger.info(f"[Functioning] 프롬프트: {len(dynamic_prompt)}자 ({_time.monotonic()-_tp0:.2f}s)")

    # ── 유저 메시지 ──
    if user_text:
        user_content = f"{user_name}: {user_text}"
    else:
        user_content = f"({user_name}이 빈 멘션을 보냈다.)"

    self._current_attachments = attachments or []
    _tool_used = set()  # 도구 1회 제한
    file_to_send = None

    def _make_config(temp=0.5):
        """사용한 도구를 제외한 config 생성."""
        all_decls = functioning_tools[0].function_declarations
        filtered = [d for d in all_decls if d.name not in _tool_used]
        tools = [types.Tool(function_declarations=filtered)] if filtered else None
        return types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            tools=tools,
            max_output_tokens=500,  # 도구 결과 요약만 (thin)
            temperature=temp,
        )

    config = _make_config(0.5)

    # Processor는 히스토리 없이 단발 호출
    loop_contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

    logger.info(f"[Functioning] API 호출 (temp=0.5)")
    response = await self._call_gemini(loop_contents, config)
    logger.info("[Functioning] API 응답 수신")

    # ── 도구 루프 (최대 10회) ──
    for loop_i in range(10):
        if not response.candidates or not response.candidates[0].content.parts:
            logger.info(f"[Functioning] 루프 {loop_i+1}: 빈 응답 (candidates 없음)")
            break

        fc = None
        for part in response.candidates[0].content.parts:
            if part.function_call:
                fc = part.function_call
                break
        if not fc:
            logger.info(f"[Functioning] 루프 {loop_i+1}: 텍스트 응답 → 루프 종료")
            break

        logger.info(f"[Functioning] 루프 {loop_i+1}: 도구 호출 {fc.name}({fc.args})")
        _meta["tools_called"].append(fc.name)

        # web_search 특수 처리
        if fc.name == "web_search":
            query = (dict(fc.args) if fc.args else {}).get("query", user_text)
            logger.info(f"[Functioning] 웹 검색: {query}")

            # 캐시 확인
            cached = await self.librarian_db.get_web_by_query(query)
            if cached:
                logger.info(f"[Functioning] 웹 캐시 히트: {query}")
                web_ids.append(cached["id"])
                tool_data = {"result": cached["result"]}
                _meta["tool_results"].append(f"web_cache:{cached['result']}")
                loop_contents.append(response.candidates[0].content)
                loop_contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name="web_search", response=tool_data)],
                ))
                try:
                    response = await self._call_gemini(loop_contents, config)
                except Exception as e:
                    logger.warning(f"[Functioning] 웹 캐시 후 API 에러: {e}")
                    break
                continue

            google_search_tool = _tools.google_search_tool
            web_config = types.GenerateContentConfig(
                system_instruction=dynamic_prompt,
                tools=google_search_tool,
                max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                temperature=1.0,
            )
            web_query = [types.Content(role="user", parts=[types.Part.from_text(text=query)])]
            web_result = ""
            try:
                web_response = await self._call_gemini(web_query, web_config)
                web_result = self._extract_reply(web_response)
            except Exception as e:
                logger.warning(f"[Functioning] 웹 검색 실패: {e}")

            if web_result:
                logger.info(f"[Functioning] 웹 검색 결과: {web_result}")
                saved_id = await self.librarian_db.save_web_result(query, web_result, user_name)
                if saved_id:
                    web_ids.append(saved_id)
                tool_data = {"result": web_result}
            else:
                tool_data = {"result": f"'{query}' 검색 결과 없음"}

            _meta["tool_results"].append(f"web:{web_result}")
            loop_contents.append(response.candidates[0].content)
            loop_contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="web_search", response=tool_data)],
            ))
            try:
                response = await self._call_gemini(loop_contents, config)
            except Exception as e:
                logger.warning(f"[Functioning] 웹 검색 후 API 에러: {e}")
                break
            continue

        # recognize_media 특수 처리
        if fc.name == "recognize_media":
            att_idx = (dict(fc.args) if fc.args else {}).get("attachment_index", 0)
            media_result = ""
            stored_name = None
            saved_media_id = None
            if att_idx < len(self._current_attachments):
                att = self._current_attachments[att_idx]
                ct = att.content_type or ""

                cached = None
                data = None
                file_hash = None
                if ct.startswith("image/") or ct == "application/pdf":
                    data = await att.read()
                    import hashlib
                    file_hash = hashlib.sha256(data).hexdigest()
                    cached = await self.librarian_db.get_media_by_hash(file_hash) \
                          or await self.librarian_db.get_media_by_filename(att.filename)
                else:
                    cached = await self.librarian_db.get_media_by_filename(att.filename)

                if cached:
                    logger.info(f"[Functioning] 미디어 캐시 히트: {att.filename} (media_id:{cached['id']})")
                    media_result = cached["result"]
                    stored_name = cached.get("stored_name")
                    saved_media_id = cached["id"]
                elif ct.startswith("image/") or ct == "application/pdf":
                    logger.info(f"[Functioning] 미디어 인식: {att.filename} ({ct})")
                    try:
                        media_parts = [
                            types.Part.from_bytes(data=data, mime_type=ct),
                            types.Part.from_text(text="3-4줄로 핵심만 설명해."),
                        ]
                        media_config = types.GenerateContentConfig(
                            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                            temperature=0.5,
                        )
                        media_response = await self._call_gemini(
                            [types.Content(role="user", parts=media_parts)],
                            media_config,
                        )
                        media_result = self._extract_reply(media_response)
                        if media_result:
                            stored_name = None
                            try:
                                os.makedirs(MEDIA_DIR, exist_ok=True)
                                import uuid
                                ext = os.path.splitext(att.filename)[1] or ""
                                stored_name = f"{uuid.uuid4().hex}{ext}"
                                with open(os.path.join(MEDIA_DIR, stored_name), "wb") as mf:
                                    mf.write(data)
                                logger.info(f"[Functioning] 미디어 저장: {att.filename} → {stored_name}")
                            except Exception as e:
                                logger.warning(f"[Functioning] 미디어 파일 저장 실패: {e}")
                                stored_name = None
                            saved_media_id = await self.librarian_db.save_media_result(
                                att.filename, media_result, user_name=user_name,
                                uploader=user_name, stored_name=stored_name,
                                file_hash=file_hash)
                            if saved_media_id:
                                media_ids.append(saved_media_id)
                    except Exception as e:
                        logger.warning(f"[Functioning] 미디어 인식 실패: {e}")
                else:
                    media_result = f"이 파일 형식({ct})은 인식할 수 없어."
            else:
                media_result = "첨부파일이 없어."

            tool_data = {"result": media_result if media_result else "인식 실패"}
            if media_result and stored_name:
                tool_data["media_id"] = saved_media_id
            logger.info(f"[Functioning] 미디어 인식 결과: {media_result}")
            _meta["tool_results"].append(f"media:{media_result}")
            loop_contents.append(response.candidates[0].content)
            loop_contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="recognize_media", response=tool_data)],
            ))
            try:
                response = await self._call_gemini(loop_contents, config)
            except Exception as e:
                logger.warning(f"[Functioning] 미디어 인식 후 API 에러: {e}")
                break
            continue

        # recognize_link 특수 처리
        if fc.name == "recognize_link":
            url = (dict(fc.args) if fc.args else {}).get("url", "")
            link_result = ""
            parsed = parse_url(url)
            normalized = parsed["normalized"]

            _img_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp")
            _url_path = url.split("?")[0].split("#")[0].lower()
            _is_image_url = any(_url_path.endswith(ext) for ext in _img_exts)

            if _is_image_url:
                try:
                    img_parts = [
                        types.Part(file_data=types.FileData(file_uri=url)),
                        types.Part.from_text(text="이 이미지를 설명해."),
                    ]
                    img_config = types.GenerateContentConfig(max_output_tokens=500, temperature=0.5)
                    img_response = await self._call_gemini(
                        [types.Content(role="user", parts=img_parts)], img_config)
                    link_result = self._extract_reply(img_response)
                    if link_result:
                        await self.librarian_db.save_url_result(
                            normalized, url, link_result, user_name=user_name, status="done")
                        logger.info(f"[Functioning] 이미지 URL 동기 인식 완료: {url}")
                except Exception as e:
                    logger.warning(f"[Functioning] 이미지 URL 인식 실패 ({url}): {e}")

            if not link_result:
                cached = await self.librarian_db.get_url_by_normalized(normalized)
                if cached:
                    if cached.get("status") == "pending":
                        logger.info(f"[Functioning] 링크 인식 중: {url}")
                        link_result = "status:pending 아직 읽는 중. 유저에게 잠깐 기다려달라고 해."
                    elif cached.get("status") == "failed":
                        await self.librarian_db.update_url_result(normalized, "", status="pending")
                        asyncio.create_task(self._recognize_url_background(parsed, user_name))
                        link_result = "status:started 방금 읽기 시작했어. 유저에게 확인해보겠다고 해."
                        logger.info(f"[Functioning] 링크 재시도: {url}")
                    else:
                        logger.info(f"[Functioning] 링크 캐시 히트: {url}")
                        link_result = cached["result"]

            if not link_result:
                await self.librarian_db.save_url_result(
                    normalized, url, "", user_name=user_name, status="pending")
                asyncio.create_task(self._recognize_url_background(parsed, user_name))
                link_result = "status:started 방금 읽기 시작했어. 유저에게 확인해보겠다고 해."
                logger.info(f"[Functioning] 링크 인식 백그라운드 시작: {url}")

            tool_data = {"result": link_result if link_result else "인식 실패"}
            logger.info(f"[Functioning] 링크 인식 결과: {link_result}")
            _meta["tool_results"].append(f"link:{link_result}")
            loop_contents.append(response.candidates[0].content)
            loop_contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="recognize_link", response=tool_data)],
            ))
            try:
                response = await self._call_gemini(loop_contents, config)
            except Exception as e:
                logger.warning(f"[Functioning] 링크 인식 후 API 에러: {e}")
                break
            continue

        # 일반 도구 실행 (search, deliver, attach, memorize_alias, forget_alias)
        tool_args = dict(fc.args) if fc.args else {}
        if fc.name == "search":
            tool_args["_user_id"] = user_id
            tool_args["_user_name"] = user_name
            tool_args["_exclude_memory_ids"] = memory_ids
            tool_args["_exclude_web_ids"] = web_ids
            tool_args["_exclude_url_ids"] = url_ids
            tool_args["_exclude_media_ids"] = media_ids
        tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
        tool_data = json.loads(tool_result)
        logger.info(f"[Functioning] 도구 결과: {tool_result}")
        _meta["tool_results"].append(tool_result)

        if tool_data.get("_action") == "deliver":
            save_path = os.path.join(FILES_DIR, tool_data["stored_name"])
            if os.path.exists(save_path):
                file_to_send = discord.File(save_path, filename=tool_data["filename"])
                await self.library_db.increment_download(tool_data["file_id"])

        if tool_data.get("_action") == "attach":
            save_path = os.path.join(MEDIA_DIR, tool_data["stored_name"])
            if os.path.exists(save_path):
                file_to_send = discord.File(save_path, filename=tool_data["filename"])

        if tool_data.get("_action") == "share_url":
            shared_url = tool_data.get("url", "")
            if shared_url:
                _meta.setdefault("shared_urls", []).append(shared_url)

        # 사용한 도구 제거 + config 갱신
        _tool_used.add(fc.name)
        if fc.name in ("deliver", "attach"):
            _tool_used.add("deliver")
            _tool_used.add("attach")
        config = _make_config(0.5)

        loop_contents.append(response.candidates[0].content)
        loop_contents.append(types.Content(
            role="user",
            parts=[types.Part.from_function_response(name=fc.name, response=tool_data)],
        ))

        try:
            response = await self._call_gemini(loop_contents, config)
        except Exception as e:
            logger.warning(f"[Functioning] 도구 후 API 에러: {e}")
            break

    # Processor의 최종 텍스트 = 도구 결과 요약 (짧음)
    tool_results_text = self._extract_reply(response)
    return tool_results_text, file_to_send, _meta


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
        line = f"책장 entry_id:{b['id']} {b['title']}{author}{alias}"
        if b.get("description"):
            line += f"\n  {b['description']}"
        files = detail.get("files", [])
        if not files:
            continue
        for f in files:
            line += f"\n  file_id:{f['id']} {f['filename']}"
        lines.append(line)

    self._catalog_cache = "\n".join(lines)
    self._catalog_built_at = updated_at
    return self._catalog_cache


async def build_memories(self, user_name: str) -> tuple[str, list[int]]:
    """기억(learned)을 프롬프트용 텍스트로 + 포함된 ID 반환"""
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

    text = "\n\n".join(sections) if sections else "(기억 없음)"
    return text, memory_ids
