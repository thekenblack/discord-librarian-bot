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
    """Functioning: 도구 실행 + 결과 보고. (tool_results_text, files_to_send, meta) 반환."""
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
    files_to_send = []

    config = types.GenerateContentConfig(
        system_instruction=dynamic_prompt,
        tools=[types.Tool(function_declarations=functioning_tools[0].function_declarations)],
        max_output_tokens=500,
        temperature=0.5,
    )

    # Processor는 히스토리 없이 단발 호출
    loop_contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

    logger.info(f"[Functioning] API 호출 (temp=0.5)")
    response = await self._call_gemini(loop_contents, config)
    logger.info("[Functioning] API 응답 수신")

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
            logger.info(f"[Functioning] 도구: {fc.name}({fc.args})")
            _meta["tools_called"].append(fc.name)

            # deliver / attach
            tool_args = dict(fc.args) if fc.args else {}
            tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
            tool_data = json.loads(tool_result)
            _meta["tool_results"].append(tool_result)

            if tool_data.get("_action") == "deliver":
                save_path = os.path.join(FILES_DIR, tool_data["stored_name"])
                if os.path.exists(save_path):
                    files_to_send.append(discord.File(save_path, filename=tool_data["filename"]))
                    await self.library_db.increment_download(tool_data["file_id"])

            if tool_data.get("_action") == "attach":
                save_path = os.path.join(MEDIA_DIR, tool_data["stored_name"])
                if os.path.exists(save_path):
                    files_to_send.append(discord.File(save_path, filename=tool_data["filename"]))

            if tool_data.get("_action") == "share_url":
                shared_url = tool_data.get("url", "")
                if shared_url:
                    _meta.setdefault("shared_urls", []).append(shared_url)

            result_parts.append(tool_result)

    # 도구 결과 + 텍스트를 합쳐서 반환
    if result_parts:
        tool_results_text = "\n".join(result_parts)
    elif text_response:
        tool_results_text = text_response
    else:
        tool_results_text = ""

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
