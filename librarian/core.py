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
from config import FILES_DIR, MEDIA_DIR, ADMIN_IDS, LIGHTNING_ADDRESS, GEMINI_MODEL, AI_MAX_OUTPUT_TOKENS
from librarian.persona import Persona
from librarian.tools import library_tools, execute_tool
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
        self.chat_histories: dict[int, list] = {}
        self._channel_locks: dict[int, asyncio.Lock] = {}
        self._mood = MoodSystem()
        self._ready = False

        self._error_messages = set(
            persona._messages
        )

    @staticmethod
    def _normalize_url(url: str) -> str:
        """URL 정규화"""
        from urllib.parse import urlparse, parse_qs, urlencode
        url = url.strip()
        # 프로토콜 통일
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)
        # 호스트: www. m. 제거, 소문자
        host = parsed.hostname or ""
        host = host.removeprefix("www.").removeprefix("m.")
        # 경로: 끝슬래시, index.html 제거
        path = parsed.path.rstrip("/")
        if path.endswith(("/index.html", "/index.htm")):
            path = path.rsplit("/", 1)[0]
        # 쿼리: 추적 파라미터 제거
        _tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                      "fbclid", "gclid", "si", "ref", "source", "feature"}
        params = {k: v for k, v in parse_qs(parsed.query).items() if k not in _tracking}
        query = urlencode(params, doseq=True) if params else ""
        # 프래그먼트 제거
        result = host + path
        if query:
            result += "?" + query
        return result.lower()

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
            # media_results에서 캐시된 설명 조회
            import aiosqlite
            from config import LIBRARIAN_DB_PATH
            desc = ""
            media_id = None
            try:
                async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute(
                        "SELECT id, result, stored_name FROM media_results WHERE filename = ? ORDER BY id DESC LIMIT 1",
                        (att.filename,))
                    row = await cursor.fetchone()
                    if row:
                        desc = row["result"][:100]
                        if row["stored_name"]:
                            media_id = row["id"]
            except Exception as e:
                logger.warning(f"미디어 캐시 조회 실패: {att.filename}: {e}")
            if desc and media_id:
                extras.append(f"[첨부: {att.filename} | media_id:{media_id} | {desc}]")
            elif desc:
                extras.append(f"[첨부: {att.filename} | {desc}]")
            else:
                extras.append(f"[첨부: {att.filename}]")
        # 메시지 텍스트에서 URL 감지 → web_results 캐시 조회
        import re as _re
        import aiosqlite as _aiosqlite
        from config import LIBRARIAN_DB_PATH as _DB_PATH
        urls = _re.findall(r'https?://[^\s<>\"]+', msg.content or "")
        for url in urls:
            normalized = self._normalize_url(url)
            try:
                async with _aiosqlite.connect(_DB_PATH) as db:
                    db.row_factory = _aiosqlite.Row
                    cursor = await db.execute(
                        "SELECT result FROM web_results WHERE query = ? ORDER BY id DESC LIMIT 1",
                        (normalized,))
                    row = await cursor.fetchone()
                    if row:
                        extras.append(f"[링크: {url} | {row['result'][:100]}]")
            except Exception as e:
                logger.warning(f"링크 캐시 조회 실패: {url}: {e}")

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
        from config import VERSION, GIT_HASH
        logger.info(f"{self.user} 온라인! ({self.persona.name}) v{VERSION} [{GIT_HASH}] model={MODEL}")

        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=self.persona.status_text
        ))
        asyncio.create_task(bitcoin_data.start_background_update())
        self._ready = True

    async def on_message(self, message: discord.Message):
        if not self._ready:
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

        # 채널 락
        ch_id = message.channel.id
        if ch_id not in self._channel_locks:
            self._channel_locks[ch_id] = asyncio.Lock()

        async with self._channel_locks[ch_id]:
            async with message.channel.typing():
                reply_text, file_to_send, _meta = await self._ask_gemini(
                    channel_id=ch_id,
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

        logger.info(f"[{guild_name}/#{channel_name}] {message.author.display_name}(ID:{message.author.id}): {text}")
        logger.info(f"[{guild_name}/#{channel_name}] {self.persona.name}: {reply_text}")

        if file_to_send:
            await message.reply(reply_text, file=file_to_send)
        else:
            await message.reply(reply_text)

    async def _ask_gemini(self, channel_id: int, user_id: str,
                          user_name: str, user_text: str,
                          guild=None, reply_chain: list[str] = None,
                          pre_context: list[str] = None,
                          attachments: list = None,
                          seen_filenames: list[str] = None) -> tuple[str, discord.File | None, dict]:
        """Gemini에게 질문하고 응답 + 파일(있으면) + 메타 반환"""
        _meta = {"tools_called": [], "tool_results": [], "error": None}

        if channel_id not in self.chat_histories:
            self.chat_histories[channel_id] = []
        history = self.chat_histories[channel_id]

        # 프롬프트 조립: 페르소나 → 규칙(도서관+기억 포함) → 상황 → 리마인더 → 페르소나
        parts = []
        parts.append(self.persona.persona_text)

        # 도서관 목록 + 기억을 prompt에 삽입
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
        logger.info(f"대화 상대: {user_name} (ID: {user_id}) → {role}")

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

        # 비트코인 실시간 데이터
        btc_block = bitcoin_data.get_prompt_block()
        if btc_block:
            parts.append(btc_block)

        # 감정 상태
        parts.append(self._mood.get_prompt_block(user_name))

        # 채널 맥락 (답글 체인 시작점 직전 또는 멘션 직전)
        if pre_context:
            parts.append("## 직전 대화\n" + "\n".join(pre_context))
            logger.info(f"직전 대화: {len(pre_context)}건")

        # 답글 체인
        if reply_chain:
            parts.append("## 답글 흐름\n" + "\n".join(reply_chain))
            logger.info(f"답글 흐름: {len(reply_chain)}건 | {'; '.join(reply_chain)}")

        # 웹/미디어 ID 수집 (search 중복 제거용, 프롬프트에는 안 넣음)
        _, _, web_ids = await self.librarian_db.get_recent_web_results(10, user_name=user_name)
        _, _, media_ids = await self.librarian_db.get_recent_media_results(10, exclude_filenames=seen_filenames or [], user_name=user_name)

        parts.append(self.persona.reminder_text)
        parts.append(self.persona.character_text)

        dynamic_prompt = "\n\n".join(p for p in parts if p)
        logger.info(f"프롬프트 길이: {len(dynamic_prompt)}자")

        # 2차용 클린 프롬프트 (직전 대화/답글 체인 제외, 기억은 유지)
        clean_parts = [p for p in parts
                       if not p.startswith("## 직전 대화") and not p.startswith("## 답글 흐름")]
        clean_prompt = "\n\n".join(p for p in clean_parts if p)

        # 3차용 프롬프트 (직전 대화/답글 체인/기억 모두 제외)
        bare_parts = [self.persona.persona_text, prompt_no_memories, info_block,
                      self.persona.reminder_text, self.persona.persona_text]
        bare_prompt = "\n\n".join(p for p in bare_parts if p)

        # 유저 메시지
        if user_text:
            user_content = f"{user_name}: {user_text}"
        else:
            user_content = f"({user_name}이 빈 멘션을 보냈다.)"

        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))
        history_snapshot = len(history)
        self._current_attachments = attachments or []  # 롤백 지점

        file_to_send = None

        config = types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            tools=library_tools,
            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            temperature=0.8,
        )

        try:
            logger.info(f"[1차] API 호출 (temperature=0.8, 히스토리={len(history)}턴)")
            response = self._call_gemini(history, config)
            logger.info("[1차] API 응답 수신")

            # function call 루프 (최대 10회)
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

                # web_search 도구: Google Search grounding으로 검색 → 결과를 저장 + AI에 돌려줌
                if fc.name == "web_search":
                    query = (dict(fc.args) if fc.args else {}).get("query", user_text)
                    logger.info(f"AI 판단 웹 검색: {query}")
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
                        web_response = self._call_gemini(web_query, web_config)
                        web_result = self._extract_reply(web_response)
                    except Exception as e:
                        logger.warning(f"웹 검색 실패: {e}")

                    if web_result:
                        logger.info(f"웹 검색 결과: {web_result[:100]}")
                        await self.librarian_db.save_web_result(query, web_result, user_name)
                        tool_data = {"result": web_result[:500]}
                    else:
                        tool_data = {"result": f"'{query}' 검색 결과 없음"}

                    _meta["tool_results"].append(f"web:{web_result[:200]}")
                    history.append(response.candidates[0].content)
                    history.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="web_search",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = self._call_gemini(history, config)
                    except Exception as e:
                        logger.warning(f"[1차] 웹 검색 후 API 에러: {e}")
                        break
                    continue

                # recognize_media 도구: 첨부파일 이미지/PDF 인식
                if fc.name == "recognize_media":
                    att_idx = (dict(fc.args) if fc.args else {}).get("attachment_index", 0)
                    media_result = ""
                    stored_name = None
                    saved_media_id = None
                    if att_idx < len(self._current_attachments):
                        att = self._current_attachments[att_idx]
                        ct = att.content_type or ""
                        if ct.startswith("image/") or ct == "application/pdf":
                            logger.info(f"미디어 인식: {att.filename} ({ct})")
                            try:
                                data = await att.read()
                                media_parts = [
                                    types.Part.from_bytes(data=data, mime_type=ct),
                                    types.Part.from_text(text="3-4줄로 핵심만 설명해."),
                                ]
                                media_config = types.GenerateContentConfig(
                                    max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                                    temperature=0.5,
                                )
                                media_response = self._call_gemini(
                                    [types.Content(role="user", parts=media_parts)],
                                    media_config,
                                )
                                media_result = self._extract_reply(media_response)
                                if media_result:
                                    # 미디어 파일 로컬 저장
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
                                        uploader=user_name, stored_name=stored_name)
                            except Exception as e:
                                logger.warning(f"미디어 인식 실패: {e}")
                        else:
                            media_result = f"이 파일 형식({ct})은 인식할 수 없어."
                    else:
                        media_result = "첨부파일이 없어."

                    tool_data = {"result": media_result[:500] if media_result else "인식 실패"}
                    if media_result and stored_name:
                        tool_data["media_id"] = saved_media_id
                    _meta["tool_results"].append(f"media:{media_result[:200]}")
                    history.append(response.candidates[0].content)
                    history.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="recognize_media",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = self._call_gemini(history, config)
                    except Exception as e:
                        logger.warning(f"[1차] 미디어 인식 후 API 에러: {e}")
                        break
                    continue

                # recognize_link 도구: 웹페이지/영상 인식
                if fc.name == "recognize_link":
                    url = (dict(fc.args) if fc.args else {}).get("url", "")
                    link_result = ""
                    try:
                        logger.info(f"링크 인식: {url}")
                        link_parts = [
                            types.Part.from_uri(file_uri=url, mime_type="text/html"),
                            types.Part.from_text(text="3-4줄로 핵심만 설명해."),
                        ]
                        link_config = types.GenerateContentConfig(
                            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                            temperature=0.5,
                        )
                        link_response = self._call_gemini(
                            [types.Content(role="user", parts=link_parts)],
                            link_config,
                        )
                        link_result = self._extract_reply(link_response)
                        if link_result:
                            await self.librarian_db.save_web_result(self._normalize_url(url), link_result, user_name=user_name, original_url=url)
                    except Exception as e:
                        logger.warning(f"링크 인식 실패: {e}")
                        link_result = f"페이지를 열 수 없었어."

                    tool_data = {"result": link_result[:500] if link_result else "인식 실패"}
                    _meta["tool_results"].append(f"link:{link_result[:200]}")
                    history.append(response.candidates[0].content)
                    history.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="recognize_link",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = self._call_gemini(history, config)
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
                    tool_args["_exclude_media_ids"] = media_ids
                tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                tool_data = json.loads(tool_result)
                logger.info(f"도구 결과: {tool_result[:200]}")
                _meta["tool_results"].append(tool_result[:200])

                # deliver 액션
                if tool_data.get("_action") == "deliver":
                    save_path = os.path.join(FILES_DIR, tool_data["stored_name"])
                    if os.path.exists(save_path):
                        file_to_send = discord.File(save_path, filename=tool_data["filename"])
                        await self.library_db.increment_download(tool_data["file_id"])

                # attach 액션
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
                    response = self._call_gemini(history, config)
                except Exception as e:
                    logger.warning(f"[1차] 도구 후 API 에러: {e}")
                    break

            # 최종 응답 추출 (루프 실패 시 히스토리 롤백)
            reply = self._extract_reply(response)

            # [mood:XX] 태그 파싱 + 제거
            import re
            mood_match = re.search(r'\[mood:(\d+)\]', reply) if reply else None
            if mood_match:
                mood_score = int(mood_match.group(1))
                self._mood.update(user_name, mood_score)
                reply = reply.replace(mood_match.group(0), '').strip()

                # 감정 상태 로그
                global_score, global_emotion = self._mood.get_global()
                user_score, user_emotion = self._mood.get_user(user_name)
                logger.info(f"감정: AI 요청=[mood:{mood_score}] → 서버 분위기={global_score:.0f}({global_emotion}), {user_name}={user_score:.0f}({user_emotion})")

            if not reply:
                self.chat_histories[channel_id] = history[:history_snapshot]
                history = self.chat_histories[channel_id]
                logger.warning(f"[1차] 빈 응답 → 에러 처리")
                return "…미안, 지금 대답을 못 하겠어.", file_to_send, _meta
            else:
                logger.info(f"[1차] 응답: {reply[:80]}")

            # ── 반복 감지 시에만 재시도 ──────────
            def _is_repeat_reply(r):
                is_rep = self._is_repeat(history, r)
                if is_rep:
                    logger.info(f"반복 감지: {r[:50]}")
                return is_rep

            clean_message = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

            # 2차: 반복일 때만. 클린 프롬프트 + 히스토리 없이 + temperature 0.9
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
                    r = self._extract_reply(self._call_gemini(clean_message, retry_config))
                    logger.info(f"[2차] 응답: {'빈 응답' if not r else r[:80]}")
                    if r and not self._is_repeat(history, r):
                        reply = r
                except Exception as e:
                    logger.warning(f"[2차] 실패: {e}")

            # 3차: 아직 반복이면. bare + 웹 검색 + temperature 1.0
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
                    r = self._extract_reply(self._call_gemini(clean_message, web_config))
                    logger.info(f"[3차] 응답: {'빈 응답' if not r else r[:80]}")
                    if r and not self._is_repeat(history, r):
                        reply = r
                except Exception as e:
                    logger.warning(f"[3차] 실패: {e}")

            # 포기
            if _is_repeat_reply(reply):
                logger.warning("[포기] 반복 해소 실패")
                reply = ""

            if reply:
                history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
            else:
                if history and history[-1].role == "user":
                    history.pop()

            self._trim_history(channel_id)

            if len(reply) > 2000:
                reply = reply[:1997] + "..."

            return reply, file_to_send, _meta

        except ClientError as e:
            logger.error(f"Gemini ClientError: status={e.status} code={getattr(e, 'code', '?')} message={e}")
            self.chat_histories[channel_id] = []
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
            _meta["error"] = f"client_error:{e.status}"
            return self.persona.error_message, None, _meta

        except Exception as e:
            self.chat_histories[channel_id] = []
            logger.error(f"Gemini 에러: {type(e).__name__}: {e}")
            _meta["error"] = f"{type(e).__name__}"
            return self.persona.error_message, None, _meta

    def _call_gemini(self, contents, config, max_retries=3, retry_delay=1.0):
        """API 호출. 실패 시 재시도 (ServerError 등 대응)"""
        import time
        last_err = None
        for attempt in range(max_retries):
            try:
                return self._gemini_client.models.generate_content(
                    model=MODEL, contents=contents, config=config,
                )
            except ClientError as e:
                if e.status == "INVALID_ARGUMENT":
                    raise
                logger.warning(f"API 에러({e.status}), {retry_delay}초 후 재시도 ({attempt+1}/{max_retries})...")
                last_err = e
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
            except Exception as e:
                logger.warning(f"API 에러({type(e).__name__}), {retry_delay}초 후 재시도 ({attempt+1}/{max_retries})...")
                last_err = e
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        if last_err:
            raise last_err
        raise ClientError("API 호출 실패")

    async def _build_reply_chain(self, message) -> tuple[list[str], list[str], list]:
        """답글 체인을 끝까지 거슬러 올라감. 10건 초과 시 앞5+뒤5. 첨부파일명+객체도 수집."""
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

        # 10건 초과 시 앞5 + 뒤5
        if len(chain) > 10:
            head = chain[:5]
            tail = chain[-5:]
            chain = head + [f"... ({len(chain) - 10}건 생략) ..."] + tail

        return chain, seen_filenames, chain_attachments

    async def _build_pre_context(self, message, limit=10) -> list[str]:
        """답글 체인 시작점 직전 또는 멘션 직전 메시지들"""
        # 답글 체인이 있으면 체인 시작점(맨 처음)을 찾음
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

        # anchor 직전 메시지들 가져오기
        lines = []
        async for msg in anchor.channel.history(limit=limit, before=anchor):
            if self.user and msg.author.id == self.user.id:
                name = self.persona.name
            else:
                name = f"{msg.author.display_name}(<@{msg.author.id}>)"
            content = msg.content[:150]
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
        # 이모지 variation selector 제거
        text = text.replace("\ufe0f", "")
        # 모든 이모지 제거 (유니코드 이모지 범위)
        text = re.sub(r'[\U0001f300-\U0001f9ff\u2600-\u27bf\u200d\ufe0f]', '', text)
        # 커스텀 디스코드 이모지 제거 <:name:id>
        text = re.sub(r'<:\w+:\d+>', '', text)
        # 구두점 제거
        text = re.sub(r'[!?.,~…·\-–—:;\'\"(){}[\]<>]', '', text)
        # 연속 공백/줄바꿈 정리
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _is_repeat(self, history: list, reply: str, threshold: float = 0.8) -> bool:
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
                # 교집합 / 합집합 (Jaccard 유사도)
                overlap = len(curr_words & prev_words)
                total = len(curr_words | prev_words)
                if total > 0 and overlap / total >= threshold:
                    return True
        return False

    async def _build_catalog(self) -> str:
        """도서관 목록을 프롬프트용 텍스트로"""
        books = await self.library_db.list_all_books()
        if not books:
            return "(도서관이 비어있음)"
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
                continue  # 파일 없는 엔트리는 프롬프트에 안 넣음
            for f in files:
                line += f"\n  file_id:{f['id']} {f['filename']}"
            lines.append(line)
        return "\n".join(lines)

    async def _build_memories(self, user_name: str) -> tuple[str, list[int]]:
        """기억(learned)을 프롬프트용 텍스트로 + 포함된 ID 반환"""
        import aiosqlite
        from config import LIBRARIAN_DB_PATH

        def _fmt(r):
            return f"- {r['author']}: {r['content'][:150]}" if r["author"] else f"- {r['content'][:150]}"

        async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            # 현재 대화 상대가 가르쳐준 것 (최근 10건, forgotten 제외)
            cursor = await db.execute(
                "SELECT id, author, content FROM learned WHERE author LIKE ? AND (forgotten IS NULL OR forgotten = 0) ORDER BY id DESC LIMIT 10",
                (f"{user_name}%",))
            user_rows = await cursor.fetchall()

            # 나머지 최근 10건 (발화자 제외, forgotten 제외)
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

    def _trim_history(self, channel_id: int):
        """히스토리를 MAX_HISTORY 턴으로 제한"""
        history = self.chat_histories.get(channel_id)
        if history and len(history) > MAX_HISTORY:
            self.chat_histories[channel_id] = history[-MAX_HISTORY:]
