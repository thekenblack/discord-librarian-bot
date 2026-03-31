"""
AI 사서봇 - Gemini function calling으로 도서관 기능 + 잡담
"""

import os
import json
import asyncio
import discord
import logging
from google import genai
from google.genai import types
from google.genai.errors import ClientError

from library.db import LibraryDB
from librarian.db import LibrarianDB
from config import FILES_DIR, MEDIA_DIR, ADMIN_IDS, LIGHTNING_ADDRESS, GEMINI_MODEL, AI_MAX_OUTPUT_TOKENS, LOG_DIR
from librarian.persona import Persona
from librarian.tools import library_tools, execute_tool, normalize_url, parse_url
from librarian import server_log
from librarian import bitcoin_data
from librarian.mood import MoodSystem

logger = logging.getLogger("AILibrarian")

MODEL = GEMINI_MODEL
MAX_HISTORY = 20


class AILibrarianBot(discord.Client):
    def __init__(self, persona: Persona, gemini_api_key: str):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.persona = persona
        self.library_db = LibraryDB()
        self.librarian_db = LibrarianDB()
        self._gemini_client = genai.Client(api_key=gemini_api_key)
        self.chat_histories: dict[str, list] = {}  # user_id → history
        self._mood = MoodSystem()
        self._bot_ready = False
        self._bg_semaphore = asyncio.Semaphore(2)  # 백그라운드 동시 실행 제한
        self._catalog_cache: str = ""
        self._catalog_built_at: str = ""

        self._error_messages = set(
            persona._messages
        )

    async def _extract_extras(self, msg) -> str:
        """메시지에서 임베드/첨부 정보 추출 (미디어 캐시 포함)"""
        extras = []
        for embed in msg.embeds:
            parts = []
            if embed.title:
                parts.append(embed.title)
            if embed.description:
                parts.append(embed.description[:100])
            if parts:
                extras.append(f"[임베드: {' - '.join(parts)}]")
        for att in msg.attachments:
            cached = await self.librarian_db.get_media_by_filename(att.filename)
            desc = ""
            media_id = None
            if cached:
                desc = cached["result"][:200]
                if cached.get("stored_name"):
                    media_id = cached["id"]
            if desc and media_id:
                extras.append(f"[첨부: {att.filename} | media_id:{media_id} | {desc}]")
            elif desc:
                extras.append(f"[첨부: {att.filename} | {desc}]")
            else:
                extras.append(f"[첨부: {att.filename}]")

        # 메시지 텍스트에서 URL 감지 → web_results 캐시 조회
        import re as _re
        urls = _re.findall(r'https?://[^\s<>\"]+', msg.content or "")
        for url in urls:
            parsed = parse_url(url)
            normalized = parsed["normalized"]
            cached = await self.librarian_db.get_url_by_normalized(normalized)
            if cached:
                if cached.get("status") == "done":
                    extras.append(f"[링크: {url} | {cached['result'][:200]}]")
                elif cached.get("status") == "pending":
                    extras.append(f"[링크: {url} | 읽는 중]")
                elif cached.get("status") == "failed":
                    extras.append(f"[링크: {url} | 읽기 실패]")

        if hasattr(msg, 'message_snapshots') and msg.message_snapshots:
            for snap in msg.message_snapshots:
                snap_content = getattr(snap, 'content', '') or ''
                if snap_content:
                    extras.append(f"[포워드: {snap_content[:100]}]")
        return " ".join(extras)

    async def on_ready(self):
        await self.library_db.init()
        await self.librarian_db.init()
        knowledge_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")
        await self.librarian_db.load_knowledge_from_files(knowledge_dir)
        await self.librarian_db.cleanup_learned()
        await self.librarian_db.reset_stale_url_results()
        await self.librarian_db.reset_stale_book_knowledge()
        from config import VERSION, GIT_HASH
        logger.info(f"{self.user} 온라인! ({self.persona.name}) v{VERSION} [{GIT_HASH}] model={MODEL}")

        # 어드민 알림 대기열 (일정 시간 내 에러를 모아서 1회 전송)
        self._admin_notify_queue: list[str] = []
        self._admin_notify_task: asyncio.Task | None = None

        _bot = self
        class _AdminErrorHandler(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    level = record.levelname
                    location = f"{record.pathname.split('/')[-1]}:{record.lineno}"
                    msg = f"**{level}** `{location}` — {record.getMessage()[:300]}"
                    _bot._admin_notify_queue.append(msg)
                    if not _bot._admin_notify_task or _bot._admin_notify_task.done():
                        _bot._admin_notify_task = asyncio.create_task(_bot._flush_admin_notify())

        _handler = _AdminErrorHandler()
        _handler.setLevel(logging.ERROR)
        logging.getLogger().addHandler(_handler)

        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=self.persona.status_text
        ))
        asyncio.create_task(bitcoin_data.start_background_update())
        asyncio.create_task(self._learn_all_books())
        self._bot_ready = True

    async def _flush_admin_notify(self, delay: float = 10.0):
        """대기열에 모인 에러를 일정 시간 후 한 번에 전송"""
        await asyncio.sleep(delay)

        if not self._admin_notify_queue:
            return

        # 대기열 비우기
        items = list(self._admin_notify_queue)
        self._admin_notify_queue.clear()

        from datetime import datetime as dt
        import zoneinfo, io
        try:
            tz = zoneinfo.ZoneInfo(os.getenv("TZ", "Asia/Seoul"))
            today = dt.now(tz).strftime("%Y-%m-%d")
        except Exception:
            today = dt.now().strftime("%Y-%m-%d")

        log_path = os.path.join(LOG_DIR, f"bot.{today}.log")

        # 요약
        summary = f"**에러 {len(items)}건**\n" + "\n".join(items[:10])
        if len(items) > 10:
            summary += f"\n... 외 {len(items) - 10}건"

        # 로그 꼬리
        tail = ""
        try:
            with open(log_path, encoding="utf-8") as f:
                tail = "".join(f.readlines()[-30:])
        except Exception:
            pass

        content = summary
        if tail:
            content += f"\n```\n{tail[-1500:]}\n```"

        for admin_id in ADMIN_IDS:
            try:
                user = await self.fetch_user(int(admin_id))
                if not user:
                    continue
                await user.send(content[:2000])
                if os.path.exists(log_path):
                    with open(log_path, encoding="utf-8") as f:
                        buf = io.BytesIO(("\ufeff" + f.read()).encode("utf-8"))
                    await user.send(file=discord.File(buf, filename=f"bot.{today}.txt"))
            except Exception as e:
                print(f"어드민 DM 실패 ({admin_id}): {e}")

    def _queue_admin_notify(self, msg: str):
        """대기열에 알림 추가"""
        self._admin_notify_queue.append(msg)
        if not self._admin_notify_task or self._admin_notify_task.done():
            self._admin_notify_task = asyncio.create_task(self._flush_admin_notify())

    async def _recognize_url_background(self, parsed: dict, user_name: str):
        """모든 URL 백그라운드 인식. 유튜브 영상이면 자막 → FileData, 일반이면 FileData 바로."""
        async with self._bg_semaphore:
            url = parsed["original_url"]
            normalized = parsed["normalized"]
            content_id = parsed.get("content_id")
            result = ""
            loop = asyncio.get_event_loop()

            # 유튜브 영상: 자막 먼저
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

            # 자막 실패 또는 일반 URL: FileData
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

            if result:
                await self.librarian_db.update_url_result(normalized, result, status="done")
            else:
                await self.librarian_db.update_url_result(normalized, "", status="failed")

    async def _learn_all_books(self):
        """미학습 도서 일괄 학습"""
        from librarian.book_learning import learn_book
        try:
            books = await self.library_db.list_all_books()
            for book in books:
                detail = await self.library_db.get_book_detail(book["id"])
                for f in detail.get("files", []):
                    await learn_book(self.librarian_db, book["id"], book["title"], f["filename"], f["stored_name"])
        except Exception as e:
            logger.error(f"도서 일괄 학습 실패: {e}")

    async def on_message(self, message: discord.Message):
        if not self._bot_ready:
            return

        channel_name = getattr(message.channel, "name", "DM")
        guild_name = message.guild.name if message.guild else "DM"

        if message.author.bot:
            if self.user and message.author.id == self.user.id:
                server_log.log(guild=guild_name, channel=channel_name,
                               author=self.persona.name, content=message.content, is_bot=True)
            return

        text = message.content
        if message.mentions:
            for user in message.mentions:
                text = text.replace(f"<@{user.id}>", f"@{user.display_name}")
                text = text.replace(f"<@!{user.id}>", f"@{user.display_name}")

        server_log.log(guild=guild_name, channel=channel_name,
                       author=message.author.display_name, content=text)

        # 멘션 체크
        bot_mentioned = self.user and self.user in message.mentions
        role_mentioned = False
        if not bot_mentioned and self.user and message.guild:
            bot_member = message.guild.get_member(self.user.id)
            if bot_member:
                role_mentioned = any(role in message.role_mentions for role in bot_member.roles if role.name != "@everyone")

        if not bot_mentioned and not role_mentioned:
            return

        logger.info(f"[수신] {message.author.display_name}(#{getattr(message.channel, 'name', 'DM')}): {message.content[:100]}")

        # 멘션 태그 제거
        for mention in [f"<@{self.user.id}>", f"<@!{self.user.id}>"]:
            text = text.replace(mention, "")
        for role in message.role_mentions:
            text = text.replace(f"<@&{role.id}>", "")
        text = text.strip()

        # 첨부/임베드 정보 추가
        msg_extras = await self._extract_extras(message)
        if msg_extras:
            text = f"{text} {msg_extras}" if text else msg_extras

        if not text:
            text = ""

        # 답글 체인 수집 + 체인 시작점 직전 맥락
        reply_chain, seen_filenames, chain_attachments = await self._build_reply_chain(message)
        pre_context = await self._build_pre_context(message)

        # 첨부파일: 현재 메시지 + 답글 체인의 첨부파일
        all_attachments = list(message.attachments) + chain_attachments

        try:
            await message.channel.typing()
        except Exception:
            pass
        reply_text, file_to_send, _meta = await self._ask_gemini(
                    user_id=str(message.author.id),
                    user_name=message.author.display_name,
                    user_text=text,
                    guild=message.guild,
                    reply_chain=reply_chain,
                    pre_context=pre_context,
                    attachments=all_attachments,
                    seen_filenames=seen_filenames,
                )

        if not reply_text and not file_to_send:
            logger.warning("응답 없음 - 에러 메시지 출력")
            reply_text = self.persona.error_message

        # 에러 메시지면 어드민 알림 대기열에 추가
        if reply_text in self._error_messages:
            error_type = _meta.get("error", "unknown")
            channel_name_short = getattr(message.channel, "name", "DM")
            self._queue_admin_notify(
                f"에러 `{error_type}` — {message.author.display_name}(#{channel_name_short}): {message.content[:80]}"
            )

        logger.info(f"[{guild_name}/#{channel_name}] {message.author.display_name}(ID:{message.author.id}): {text}")
        logger.info(f"[{guild_name}/#{channel_name}] {self.persona.name}: {reply_text}")

        if file_to_send:
            await message.reply(reply_text, file=file_to_send)
        else:
            await message.reply(reply_text)

    async def _ask_gemini(self, user_id: str,
                          user_name: str, user_text: str,
                          guild=None, reply_chain: list[str] = None,
                          pre_context: list[str] = None,
                          attachments: list = None,
                          seen_filenames: list[str] = None) -> tuple[str, discord.File | None, dict]:
        """Gemini에게 질문하고 응답 + 파일(있으면) + 메타 반환"""
        _meta = {"tools_called": [], "tool_results": [], "error": None}

        if user_id not in self.chat_histories:
            self.chat_histories[user_id] = []
        history = self.chat_histories[user_id]

        # 프롬프트 조립
        parts = []
        parts.append(self.persona.persona_text)

        catalog = await self._build_catalog()
        memories_text, memory_ids = await self._build_memories(user_name)
        prompt = self.persona.prompt_text.replace("{library_catalog}", catalog).replace("{learned_memories}", memories_text)
        prompt_no_memories = self.persona.prompt_text.replace("{library_catalog}", catalog).replace("{learned_memories}", "(기억 없음)")
        parts.append(prompt)

        # 상황 정보
        admin_names = []
        if guild:
            for aid in ADMIN_IDS:
                member = guild.get_member(int(aid))
                if member:
                    admin_names.append(member.display_name)
        role = "주인 (도서관 관리자)" if user_id in ADMIN_IDS else "일반 방문자"
        _user_score, _user_emotion = self._mood.get_user(user_name)
        _global_score, _global_emotion = self._mood.get_global()
        logger.info(f"대화 상대: {user_name} (ID: {user_id}) → {role} | 개인={_user_score:.0f}({_user_emotion}) 서버={_global_score:.0f}({_global_emotion})")

        from datetime import datetime as dt
        import zoneinfo
        try:
            tz_name = os.getenv("TZ", "Asia/Seoul")
            tz_info = zoneinfo.ZoneInfo(tz_name)
            now = dt.now(tz_info)
            utc_offset = now.strftime("%z")
            utc_str = f"UTC{utc_offset[:3]}:{utc_offset[3:]}"
        except Exception:
            now = dt.now()
            utc_str = ""
        time_str = now.strftime('%Y년 %m월 %d일 %H:%M')
        if utc_str:
            time_str += f" ({utc_str})"
        info_block = f"## 상황\n현재: {time_str}\n대화 상대: {user_name} ({role})"
        if admin_names:
            info_block += f"\n도서관 주인: {', '.join(admin_names)}"
        if LIGHTNING_ADDRESS:
            info_block += f"\n후원 라이트닝 주소: {LIGHTNING_ADDRESS}"
        parts.append(info_block)

        btc_block = bitcoin_data.get_prompt_block()
        if btc_block:
            parts.append(btc_block)

        parts.append(self._mood.get_prompt_block(user_name))

        if pre_context:
            parts.append("## 직전 대화\n" + "\n".join(pre_context))
            logger.info(f"직전 대화: {len(pre_context)}건")

        if reply_chain:
            parts.append("## 답글 흐름\n" + "\n".join(reply_chain))
            logger.info(f"답글 흐름: {len(reply_chain)}건 | {'; '.join(reply_chain)}")

        web_user, web_other, web_ids = await self.librarian_db.get_recent_web_results(10, user_name=user_name)
        media_user, media_other, media_ids = await self.librarian_db.get_recent_media_results(10, exclude_filenames=seen_filenames or [], user_name=user_name)
        url_user, url_other, url_ids = await self.librarian_db.get_recent_url_results(10, user_name=user_name)
        cache_lines = []
        web_all = web_user + web_other
        if web_all:
            cache_lines.append("[웹] " + " | ".join(f"{w['query']}: {w['result'][:200]}" for w in web_all))
        url_all = url_user + url_other
        if url_all:
            cache_lines.append("[URL] " + " | ".join(f"{u['original_url']}: {u['result'][:200]}" for u in url_all))
        media_all = media_user + media_other
        if media_all:
            cache_lines.append("[미디어] " + " | ".join(f"media_id:{m['id']} {m['filename']}: {m['result'][:200]}" for m in media_all))
        if cache_lines:
            parts.append("## 최근 인식\n" + "\n".join(cache_lines))

        parts.append(self.persona.reminder_text)
        parts.append(self.persona.character_text)

        dynamic_prompt = "\n\n".join(p for p in parts if p)
        logger.info(f"프롬프트 길이: {len(dynamic_prompt)}자")

        clean_parts = [p for p in parts
                       if not p.startswith("## 직전 대화") and not p.startswith("## 답글 흐름")]
        clean_prompt = "\n\n".join(p for p in clean_parts if p)

        bare_parts = [self.persona.persona_text, prompt_no_memories, info_block,
                      self.persona.reminder_text, self.persona.persona_text]
        bare_prompt = "\n\n".join(p for p in bare_parts if p)

        if user_text:
            user_content = f"{user_name}: {user_text}"
        else:
            user_content = f"({user_name}이 빈 멘션을 보냈다.)"

        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))
        history_snapshot = len(history)
        self._current_attachments = attachments or []

        file_to_send = None

        config = types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            tools=library_tools,
            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            temperature=0.8,
        )

        try:
            logger.info(f"[1차] API 호출 (temperature=0.8, 히스토리={len(history)}턴)")
            response = await self._call_gemini(history, config)
            logger.info("[1차] API 응답 수신")

            for loop_i in range(10):
                if not response.candidates or not response.candidates[0].content.parts:
                    logger.info(f"[1차] 루프 {loop_i+1}: 빈 응답 (candidates 없음)")
                    break

                fc = None
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        fc = part.function_call
                        break
                if not fc:
                    logger.info(f"[1차] 루프 {loop_i+1}: 텍스트 응답 → 루프 종료")
                    break

                logger.info(f"[1차] 루프 {loop_i+1}: 도구 호출 {fc.name}({fc.args})")
                _meta["tools_called"].append(fc.name)

                if fc.name == "web_search":
                    query = (dict(fc.args) if fc.args else {}).get("query", user_text)
                    logger.info(f"AI 판단 웹 검색: {query}")

                    # 캐시 확인 — 히트면 바로 반환
                    cached = await self.librarian_db.get_web_by_query(query)
                    if cached:
                        logger.info(f"웹 캐시 히트: {query}")
                        web_ids.append(cached["id"])
                        tool_data = {"result": cached["result"]}
                        _meta["tool_results"].append(f"web_cache:{cached['result']}")
                        history.append(response.candidates[0].content)
                        history.append(types.Content(
                            role="user",
                            parts=[types.Part.from_function_response(
                                name="web_search",
                                response=tool_data,
                            )],
                        ))
                        try:
                            response = await self._call_gemini(history, config)
                        except Exception as e:
                            logger.warning(f"[1차] 웹 캐시 후 API 에러: {e}")
                            break
                        continue

                    from librarian.tools import google_search_tool
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
                        logger.warning(f"웹 검색 실패: {e}")

                    if web_result:
                        logger.info(f"웹 검색 결과: {web_result}")
                        saved_id = await self.librarian_db.save_web_result(query, web_result, user_name)
                        if saved_id:
                            web_ids.append(saved_id)
                        tool_data = {"result": web_result}
                    else:
                        tool_data = {"result": f"'{query}' 검색 결과 없음"}

                    _meta["tool_results"].append(f"web:{web_result}")
                    history.append(response.candidates[0].content)
                    history.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="web_search",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = await self._call_gemini(history, config)
                    except Exception as e:
                        logger.warning(f"[1차] 웹 검색 후 API 에러: {e}")
                        break
                    continue

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
                            logger.info(f"미디어 캐시 히트: {att.filename} (media_id:{cached['id']})")
                            media_result = cached["result"]
                            stored_name = cached.get("stored_name")
                            saved_media_id = cached["id"]
                        elif ct.startswith("image/") or ct == "application/pdf":
                            logger.info(f"미디어 인식: {att.filename} ({ct})")
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
                                        logger.info(f"미디어 저장: {att.filename} → {stored_name}")
                                    except Exception as e:
                                        logger.warning(f"미디어 파일 저장 실패: {e}")
                                        stored_name = None
                                    saved_media_id = await self.librarian_db.save_media_result(
                                        att.filename, media_result, user_name=user_name,
                                        uploader=user_name, stored_name=stored_name,
                                        file_hash=file_hash)
                                    if saved_media_id:
                                        media_ids.append(saved_media_id)
                            except Exception as e:
                                logger.warning(f"미디어 인식 실패: {e}")
                        else:
                            media_result = f"이 파일 형식({ct})은 인식할 수 없어."
                    else:
                        media_result = "첨부파일이 없어."

                    tool_data = {"result": media_result if media_result else "인식 실패"}
                    if media_result and stored_name:
                        tool_data["media_id"] = saved_media_id
                    logger.info(f"미디어 인식 결과: {media_result}")
                    _meta["tool_results"].append(f"media:{media_result}")
                    history.append(response.candidates[0].content)
                    history.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="recognize_media",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = await self._call_gemini(history, config)
                    except Exception as e:
                        logger.warning(f"[1차] 미디어 인식 후 API 에러: {e}")
                        break
                    continue

                if fc.name == "recognize_link":
                    url = (dict(fc.args) if fc.args else {}).get("url", "")
                    link_result = ""
                    parsed = parse_url(url)
                    normalized = parsed["normalized"]

                    cached = await self.librarian_db.get_url_by_normalized(normalized)
                    if cached:
                        if cached.get("status") == "pending":
                            logger.info(f"링크 인식 중: {url}")
                            link_result = "status:pending 아직 읽는 중. 유저에게 잠깐 기다려달라고 해."
                        elif cached.get("status") == "failed":
                            await self.librarian_db.update_url_result(normalized, "", status="pending")
                            asyncio.create_task(self._recognize_url_background(parsed, user_name))
                            link_result = "status:started 방금 읽기 시작했어. 유저에게 확인해보겠다고 해."
                            logger.info(f"링크 재시도: {url}")
                        else:
                            logger.info(f"링크 캐시 히트: {url}")
                            link_result = cached["result"]

                    if not cached:
                        await self.librarian_db.save_url_result(
                            normalized, url, "", user_name=user_name, status="pending")
                        asyncio.create_task(self._recognize_url_background(parsed, user_name))
                        link_result = "status:started 방금 읽기 시작했어. 유저에게 확인해보겠다고 해."
                        logger.info(f"링크 인식 백그라운드 시작: {url}")

                    tool_data = {"result": link_result if link_result else "인식 실패"}
                    logger.info(f"링크 인식 결과: {link_result}")
                    _meta["tool_results"].append(f"link:{link_result}")
                    history.append(response.candidates[0].content)
                    history.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="recognize_link",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = await self._call_gemini(history, config)
                    except Exception as e:
                        logger.warning(f"[1차] 링크 인식 후 API 에러: {e}")
                        break
                    continue

                # 일반 도구 실행
                tool_args = dict(fc.args) if fc.args else {}
                if fc.name in ("search", "save_memory", "modify_memory"):
                    tool_args["_user_id"] = user_id
                    tool_args["_user_name"] = user_name
                if fc.name == "search":
                    tool_args["_exclude_memory_ids"] = memory_ids
                    tool_args["_exclude_web_ids"] = web_ids
                    tool_args["_exclude_url_ids"] = url_ids
                    tool_args["_exclude_media_ids"] = media_ids
                tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                tool_data = json.loads(tool_result)
                logger.info(f"도구 결과: {tool_result}")
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

                history.append(response.candidates[0].content)
                history.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=fc.name,
                        response=tool_data,
                    )],
                ))

                try:
                    response = await self._call_gemini(history, config)
                except Exception as e:
                    logger.warning(f"[1차] 도구 후 API 에러: {e}")
                    break

            reply = self._extract_reply(response)

            import re
            _mood_applied = False

            def _apply_mood(text):
                """[mood:XX] 또는 [mood:+X]/[mood:-X] 태그 파싱 + 제거. 첫 1회만 update."""
                nonlocal _mood_applied
                m = re.search(r'\[mood:([+-]?\d+)\]', text) if text else None
                if m:
                    text = text.replace(m.group(0), '').strip()
                    if not _mood_applied:
                        raw = m.group(1)
                        try:
                            val = int(raw)
                            relative = raw.startswith("+") or raw.startswith("-")
                            self._mood.update(user_name, val, relative=relative)
                            _mood_applied = True
                            global_score, global_emotion = self._mood.get_global()
                            user_score, user_emotion = self._mood.get_user(user_name)
                            logger.info(f"감정: AI 요청=[mood:{raw}] → 서버 분위기={global_score:.0f}({global_emotion}), {user_name}={user_score:.0f}({user_emotion})")
                        except ValueError:
                            logger.warning(f"mood 파싱 실패: {m.group(0)}")
                return text

            reply = _apply_mood(reply)

            # 텍스트에 함수 호출 패턴이 섞여 있을 때 감지 후 실행
            _TOOL_NAMES = {
                "search", "deliver", "save_memory", "add_knowledge", "add_entry_alias",
                "web_search", "add_alias", "forget_alias", "forget_memory", "modify_memory",
                "recognize_media", "recognize_link", "attach",
            }
            if reply:
                _inline_pattern = re.compile(
                    r'(' + '|'.join(re.escape(t) for t in _TOOL_NAMES) + r')\s*\(([^)]*)\)',
                    re.DOTALL
                )
                _inline_match = _inline_pattern.search(reply)
                if _inline_match:
                    _before = reply[:_inline_match.start()].strip()
                    _tool_name = _inline_match.group(1)
                    _args_raw = _inline_match.group(2).strip()
                    logger.info(f"인라인 함수 감지: {_tool_name}({_args_raw[:80]})")

                    # args 파싱
                    _POSITIONAL_MAP = {
                        "deliver": "file_id",
                        "attach": "media_id",
                        "recognize_media": "attachment_index",
                        "recognize_link": "url",
                        "search": "keyword",
                        "web_search": "query",
                        "save_memory": "content",
                        "add_knowledge": "content",
                        "forget_memory": "keyword",
                        "forget_alias": "alias_id",
                    }
                    _tool_args = {}
                    # key: value 또는 key=value 형태
                    _kv_matches = list(re.finditer(r'(\w+)\s*[:=]\s*(.+?)(?=,\s*\w+\s*[:=]|$)', _args_raw, re.DOTALL))
                    if _kv_matches:
                        for _m in _kv_matches:
                            _k = _m.group(1).strip()
                            _v = _m.group(2).strip().strip('"\'')
                            _tool_args[_k] = _v
                    elif _args_raw and _tool_name in _POSITIONAL_MAP:
                        # positional: deliver(5) → {"file_id": 5}
                        _val = _args_raw.strip().strip('"\'')
                        try:
                            _val = int(_val)
                        except ValueError:
                            pass
                        _tool_args[_POSITIONAL_MAP[_tool_name]] = _val

                    if _tool_name in ("search", "save_memory", "modify_memory"):
                        _tool_args["_user_id"] = user_id
                        _tool_args["_user_name"] = user_name
                    if _tool_name == "search":
                        _tool_args["_exclude_memory_ids"] = memory_ids
                        _tool_args["_exclude_web_ids"] = web_ids
                        _tool_args["_exclude_url_ids"] = url_ids
                        _tool_args["_exclude_media_ids"] = media_ids

                    try:
                        _tool_result = await execute_tool(self.library_db, self.librarian_db, _tool_name, _tool_args)
                        _tool_data = json.loads(_tool_result)
                        logger.info(f"인라인 함수 실행 결과: {_tool_result[:100]}")

                        # history에 function call/response 추가
                        history.append(types.Content(role="model", parts=[
                            types.Part.from_text(text=reply),
                        ]))
                        history.append(types.Content(role="user", parts=[
                            types.Part.from_function_response(name=_tool_name, response=_tool_data),
                        ]))

                        if _before:
                            # 앞부분 텍스트 있으면 reply로 쓰고 함수 결과로 재응답
                            try:
                                _follow_response = await self._call_gemini(history, config)
                                _follow_reply = self._extract_reply(_follow_response)
                                if _follow_reply:
                                    reply = _before + "\n" + _follow_reply
                                else:
                                    reply = _before
                            except Exception as _e:
                                logger.warning(f"인라인 함수 후 재응답 실패: {_e}")
                                reply = _before
                        else:
                            # 앞부분 없으면 함수 결과로만 재응답
                            try:
                                _follow_response = await self._call_gemini(history, config)
                                _follow_reply = self._extract_reply(_follow_response)
                                if _follow_reply:
                                    reply = _follow_reply
                            except Exception as _e:
                                logger.warning(f"인라인 함수 후 재응답 실패: {_e}")
                                reply = ""
                    except Exception as _e:
                        logger.warning(f"인라인 함수 실행 실패 ({_tool_name}): {_e}")

                    # 인라인 함수 재응답에서 mood 태그 제거
                    reply = _apply_mood(reply)

            if not reply:
                self.chat_histories[user_id] = history[:history_snapshot]
                history = self.chat_histories[user_id]
                logger.warning(f"[1차] 빈 응답 → 에러 처리")
                return "…미안, 지금 대답을 못 하겠어.", file_to_send, _meta
            else:
                logger.info(f"[1차] 응답: {reply}")

            def _is_repeat_reply(r):
                is_rep = self._is_repeat(history, r)
                if is_rep:
                    logger.info(f"반복 감지: {r[:50]}")
                return is_rep

            clean_message = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

            if _is_repeat_reply(reply):
                logger.warning(f"[2차] 시도 (클린, 0.9)")
                try:
                    retry_config = types.GenerateContentConfig(
                        system_instruction=clean_prompt,
                        tools=library_tools,
                        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                        temperature=0.9,
                    )
                    logger.info("[2차] API 호출")
                    r = self._extract_reply(await self._call_gemini(clean_message, retry_config))
                    logger.info(f"[2차] 응답: {'빈 응답' if not r else r}")
                    if r and not self._is_repeat(history, r):
                        reply = r
                except Exception as e:
                    logger.warning(f"[2차] 실패: {e}")

            if _is_repeat_reply(reply):
                logger.warning(f"[3차] 시도 (bare+웹, 1.0)")
                try:
                    from librarian.tools import google_search_tool
                    web_config = types.GenerateContentConfig(
                        system_instruction=bare_prompt,
                        tools=google_search_tool,
                        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                        temperature=1.0,
                    )
                    logger.info("[3차] API 호출")
                    r = self._extract_reply(await self._call_gemini(clean_message, web_config))
                    logger.info(f"[3차] 응답: {'빈 응답' if not r else r}")
                    if r and not self._is_repeat(history, r):
                        reply = r
                except Exception as e:
                    logger.warning(f"[3차] 실패: {e}")

            if _is_repeat_reply(reply):
                logger.warning("[포기] 반복 해소 실패")
                reply = ""

            # 2-3차 결과에도 인라인 함수 패턴이 있으면 실행 + 제거
            if reply:
                _inline_pattern_retry = re.compile(
                    r'(' + '|'.join(re.escape(t) for t in _TOOL_NAMES) + r')\s*\(([^)]*)\)',
                    re.DOTALL
                )
                _inline_match_retry = _inline_pattern_retry.search(reply)
                if _inline_match_retry:
                    _tool_name_r = _inline_match_retry.group(1)
                    _args_raw_r = _inline_match_retry.group(2).strip()
                    logger.info(f"[재시도] 인라인 함수 감지: {_tool_name_r}({_args_raw_r[:80]})")

                    _tool_args_r = {}
                    _kv_r = list(re.finditer(r'(\w+)\s*[:=]\s*(.+?)(?=,\s*\w+\s*[:=]|$)', _args_raw_r, re.DOTALL))
                    if _kv_r:
                        for _m in _kv_r:
                            _tool_args_r[_m.group(1).strip()] = _m.group(2).strip().strip('"\'')
                    elif _args_raw_r and _tool_name_r in _POSITIONAL_MAP:
                        _val = _args_raw_r.strip().strip('"\'')
                        try:
                            _val = int(_val)
                        except ValueError:
                            pass
                        _tool_args_r[_POSITIONAL_MAP[_tool_name_r]] = _val

                    try:
                        _tool_result_r = await execute_tool(self.library_db, self.librarian_db, _tool_name_r, _tool_args_r)
                        _tool_data_r = json.loads(_tool_result_r)
                        logger.info(f"[재시도] 인라인 함수 실행: {_tool_result_r[:100]}")

                        if _tool_data_r.get("_action") == "deliver":
                            save_path = os.path.join(FILES_DIR, _tool_data_r["stored_name"])
                            if os.path.exists(save_path):
                                file_to_send = discord.File(save_path, filename=_tool_data_r["filename"])
                                await self.library_db.increment_download(_tool_data_r["file_id"])
                        elif _tool_data_r.get("_action") == "attach":
                            save_path = os.path.join(MEDIA_DIR, _tool_data_r["stored_name"])
                            if os.path.exists(save_path):
                                file_to_send = discord.File(save_path, filename=_tool_data_r["filename"])
                    except Exception as _e:
                        logger.warning(f"[재시도] 인라인 함수 실행 실패: {_e}")

                    # 텍스트에서 함수 호출 제거
                    reply = reply[:_inline_match_retry.start()].strip()
                    if not reply:
                        reply = f"여기 있어."

            if reply:
                history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
            else:
                if history and history[-1].role == "user":
                    history.pop()

            self._trim_history(user_id)

            if len(reply) > 2000:
                reply = reply[:1997] + "..."

            return reply, file_to_send, _meta

        except ClientError as e:
            logger.error(f"Gemini ClientError: status={e.status} code={getattr(e, 'code', '?')} message={e}")
            self.chat_histories[user_id] = []
            if e.status == "RESOURCE_EXHAUSTED":
                msg = str(e)
                if "PerDay" in msg or "per_day" in msg:
                    logger.warning("일일 한도 초과 (모든 키 소진)")
                    _meta["error"] = "daily_limit"
                    return self.persona.error_message, None, _meta
                else:
                    logger.warning("분당 한도 초과")
                    _meta["error"] = "rate_limit"
                    return self.persona.error_message, None, _meta
            if e.status == "INVALID_ARGUMENT":
                logger.warning("INVALID_ARGUMENT → 히스토리 초기화 후 클린 재시도")
                try:
                    clean_message = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
                    retry_config = types.GenerateContentConfig(
                        system_instruction=dynamic_prompt,
                        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                        temperature=0.8,
                    )
                    response = await self._call_gemini(clean_message, retry_config)
                    reply = self._extract_reply(response)
                    if reply:
                        reply = _apply_mood(reply)
                        logger.info(f"[클린 재시도] 응답: {reply}")
                        return reply, None, _meta
                except Exception as retry_e:
                    logger.warning(f"[클린 재시도] 실패: {retry_e}")
            _meta["error"] = f"client_error:{e.status}"
            return self.persona.error_message, None, _meta

        except Exception as e:
            self.chat_histories[user_id] = []
            logger.error(f"Gemini 에러: {type(e).__name__}: {e}")
            _meta["error"] = f"{type(e).__name__}"
            return self.persona.error_message, None, _meta

    async def _call_gemini(self, contents, config, max_retries=3, retry_delay=1.0):
        """API 호출 (비동기). 실패 시 재시도."""
        last_err = None
        loop = asyncio.get_event_loop()
        for attempt in range(max_retries):
            try:
                return await loop.run_in_executor(
                    None,
                    lambda: self._gemini_client.models.generate_content(
                        model=MODEL, contents=contents, config=config,
                    ),
                )
            except ClientError as e:
                if e.status == "INVALID_ARGUMENT":
                    raise
                logger.warning(f"API 에러({e.status}), {retry_delay}초 후 재시도 ({attempt+1}/{max_retries})...")
                last_err = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
            except Exception as e:
                logger.warning(f"API 에러({type(e).__name__}), {retry_delay}초 후 재시도 ({attempt+1}/{max_retries})...")
                last_err = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
        if last_err:
            raise last_err
        raise ClientError("API 호출 실패")

    async def _build_reply_chain(self, message) -> tuple[list[str], list[str], list]:
        """답글 체인을 끝까지 거슬러 올라감. 10건 초과 시 앞5+뒤5."""
        chain = []
        seen_filenames = []
        chain_attachments = []
        current = message
        while current.reference:
            ref = current.reference.resolved
            if not ref and current.reference.message_id:
                try:
                    ref = await current.channel.fetch_message(current.reference.message_id)
                except Exception:
                    break
            if not ref:
                break
            if self.user and ref.author.id == self.user.id:
                name = self.persona.name
            else:
                name = f"{ref.author.display_name}(<@{ref.author.id}>)"
            content = ref.content[:150]
            extras = await self._extract_extras(ref)
            if extras:
                content = f"{content} {extras}" if content else extras
            for att in ref.attachments:
                seen_filenames.append(att.filename)
                chain_attachments.append(att)
            chain.append(f"{name}: {content}")
            current = ref
        chain.reverse()

        if len(chain) > 10:
            head = chain[:5]
            tail = chain[-5:]
            chain = head + [f"... ({len(chain) - 10}건 생략) ..."] + tail

        return chain, seen_filenames, chain_attachments

    async def _build_pre_context(self, message, limit=10) -> list[str]:
        """답글 체인 시작점 직전 또는 멘션 직전 메시지들"""
        anchor = message
        while anchor.reference:
            ref = anchor.reference.resolved
            if not ref and anchor.reference.message_id:
                try:
                    ref = await anchor.channel.fetch_message(anchor.reference.message_id)
                except Exception:
                    break
            if not ref:
                break
            anchor = ref

        lines = []
        async for msg in anchor.channel.history(limit=limit, before=anchor):
            if self.user and msg.author.id == self.user.id:
                name = self.persona.name
            else:
                name = f"{msg.author.display_name}(<@{msg.author.id}>)"
            content = msg.content[:150]
            extras = await self._extract_extras(msg)
            if extras:
                content = f"{content} {extras}" if content else extras
            lines.append(f"{name}: {content}")
        lines.reverse()
        return lines

    @staticmethod
    def _extract_reply(response) -> str:
        """Gemini 응답에서 텍스트 추출"""
        if not response.candidates or not response.candidates[0].content.parts:
            return ""
        parts = []
        for part in response.candidates[0].content.parts:
            if part.text:
                text = part.text.strip()
                if text:
                    parts.append(text)
        return "\n".join(parts) if parts else ""

    @staticmethod
    def _normalize_for_compare(text: str) -> str:
        """비교용 정규화: 이모지, 공백, 줄바꿈 정리"""
        import re
        text = text.replace("\ufe0f", "")
        text = re.sub(r'[\U0001f300-\U0001f9ff\u2600-\u27bf\u200d\ufe0f]', '', text)
        text = re.sub(r'<:\w+:\d+>', '', text)
        text = re.sub(r'[!?.,~…·\-–—:;\'\"(){}[\]<>]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _is_repeat(self, history: list, reply: str, threshold: float = 0.9) -> bool:
        """히스토리 내 봇 답변과 유사한 게 있는지 확인 (단어 기반 유사도)"""
        curr = self._normalize_for_compare(reply)
        if not curr:
            return False
        curr_words = set(curr.split())
        if not curr_words:
            return False
        for h in history:
            if h.role == "model" and h.parts and h.parts[0].text:
                prev = self._normalize_for_compare(h.parts[0].text)
                prev_words = set(prev.split())
                if not prev_words:
                    continue
                overlap = len(curr_words & prev_words)
                total = len(curr_words | prev_words)
                if total > 0 and overlap / total >= threshold:
                    return True
        return False

    async def _build_catalog(self) -> str:
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

    async def _build_memories(self, user_name: str) -> tuple[str, list[int]]:
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

    def _trim_history(self, user_id: str):
        """히스토리를 MAX_HISTORY 턴으로 제한. function_call/response 쌍 보장."""
        history = self.chat_histories.get(user_id)
        if history and len(history) > MAX_HISTORY:
            trimmed = history[-MAX_HISTORY:]
            while trimmed and trimmed[0].role == "user" and trimmed[0].parts:
                has_fn_response = any(hasattr(p, 'function_response') and p.function_response for p in trimmed[0].parts)
                if has_fn_response:
                    trimmed.pop(0)
                else:
                    break
            self.chat_histories[user_id] = trimmed