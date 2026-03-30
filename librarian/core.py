"""
AI 사서봇 - Gemini function calling으로 도서관 기능 + 잡담
"""

import os
import json
import asyncio
import discord
import logging
from datetime import date, timedelta
from google import genai
from google.genai import types
from google.genai.errors import ClientError

from library.db import LibraryDB
from librarian.db import LibrarianDB
from config import FILES_DIR, ADMIN_IDS, LIGHTNING_ADDRESS, GEMINI_MODEL, AI_BUFFER_SIZE, AI_MAX_OUTPUT_TOKENS
from librarian.persona import Persona
from librarian.tools import library_tools, execute_tool
from librarian import chat_log
from librarian import server_log

logger = logging.getLogger("AILibrarian")

MODEL = GEMINI_MODEL
MAX_HISTORY = 20


class AILibrarianBot(discord.Client):
    def __init__(self, persona: Persona, gemini_api_keys: list[str]):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.persona = persona
        self.library_db = LibraryDB()
        self.librarian_db = LibrarianDB()
        self._gemini_clients = [genai.Client(api_key=k) for k in gemini_api_keys]
        self._client_index = 0
        self._dead_until: dict[int, date] = {}
        self.chat_histories: dict[int, list] = {}
        self._channel_locks: dict[int, asyncio.Lock] = {}
        self._ready = False

        self._error_messages = set(
            persona._error_messages + persona._rate_limit_messages + persona._daily_limit_messages
        )

    @staticmethod
    def _extract_extras(msg) -> str:
        """메시지에서 임베드/첨부 정보 추출"""
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
            extras.append(f"[첨부: {att.filename}]")
        return " ".join(extras)

    async def on_ready(self):
        await self.library_db.init()
        await self.librarian_db.init()
        knowledge_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")
        await self.librarian_db.load_knowledge_from_files(knowledge_dir)
        await self.librarian_db.cleanup_learned()
        from config import VERSION, GIT_HASH
        logger.info(f"{self.user} 온라인! ({self.persona.name}) v{VERSION} [{GIT_HASH}] model={MODEL}")
        chat_log.log_startup(self.persona.name, self.persona.prompt_text, len(self._gemini_clients))
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=self.persona.status_text
        ))
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
        msg_extras = self._extract_extras(message)
        if msg_extras:
            text = f"{text} {msg_extras}" if text else msg_extras

        if not text:
            text = ""

        # 답글 맥락
        if message.reference:
            ref_msg = message.reference.resolved
            if not ref_msg and message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except Exception:
                    pass
            if ref_msg:
                ref_content = ref_msg.content[:100]
                for u in ref_msg.mentions:
                    ref_content = ref_content.replace(f"<@{u.id}>", f"@{u.display_name}")
                    ref_content = ref_content.replace(f"<@!{u.id}>", f"@{u.display_name}")
                ref_extras = self._extract_extras(ref_msg)
                if ref_extras:
                    ref_content = f"{ref_content} {ref_extras}" if ref_content else ref_extras
                if ref_msg.content not in self._error_messages:
                    ref_name = self.persona.name if (self.user and ref_msg.author.id == self.user.id) else ref_msg.author.display_name
                    text = f"[원본: {ref_name}이 쓴 \"{ref_content}\"] {text}"

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
                )

        if not reply_text and not file_to_send:
            logger.warning("응답 없음 - 에러 메시지 출력")
            reply_text = self.persona.error_message

        logger.info(f"[{guild_name}/#{channel_name}] {message.author.display_name}(ID:{message.author.id}): {text}")
        logger.info(f"[{guild_name}/#{channel_name}] {self.persona.name}: {reply_text}")

        chat_log.log_chat(
            guild=guild_name,
            channel=channel_name,
            user_id=str(message.author.id),
            user_name=message.author.display_name,
            user_text=text,
            reply_text=reply_text,
            tools_called=_meta["tools_called"],
            tool_results=_meta["tool_results"],
            has_file=file_to_send is not None,
            retries=0,
            web_search=False,
            error=_meta["error"],
        )

        if file_to_send:
            await message.reply(reply_text, file=file_to_send)
        else:
            await message.reply(reply_text)

    async def _ask_gemini(self, channel_id: int, user_id: str,
                          user_name: str, user_text: str,
                          guild=None) -> tuple[str, discord.File | None, dict]:
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
        memories = await self._build_memories()
        prompt = self.persona.prompt_text.replace("{library_catalog}", catalog).replace("{learned_memories}", memories)
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

        parts.append(self.persona.reminder_text)
        parts.append(self.persona.persona_text)

        dynamic_prompt = "\n\n".join(p for p in parts if p)
        logger.info(f"프롬프트 길이: {len(dynamic_prompt)}자")

        # 유저 메시지
        if user_text:
            user_content = f"{user_name}: {user_text}"
        else:
            user_content = f"({user_name}이 빈 멘션을 보냈다.)"

        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))

        file_to_send = None

        config = types.GenerateContentConfig(
            system_instruction=dynamic_prompt,
            tools=library_tools,
            max_output_tokens=AI_MAX_OUTPUT_TOKENS,
            temperature=0.8,
        )

        try:
            response = self._call_gemini(history, config)

            # function call 루프 (최대 5회)
            for _ in range(5):
                if not response.candidates or not response.candidates[0].content.parts:
                    break

                fc = None
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        fc = part.function_call
                        break
                if not fc:
                    break

                logger.info(f"도구 호출: {fc.name}({fc.args})")
                _meta["tools_called"].append(fc.name)

                # web_search 도구: Google Search grounding으로 전환
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
                    web_history = list(history) + [
                        response.candidates[0].content,
                        types.Content(role="user", parts=[types.Part.from_function_response(
                            name="web_search",
                            response={"instruction": f"Google에서 '{query}'를 검색해서 구체적인 정보를 답변에 포함해."},
                        )]),
                    ]
                    try:
                        web_response = self._call_gemini(web_history, web_config)
                        if web_response.candidates and web_response.candidates[0].content.parts:
                            for p in web_response.candidates[0].content.parts:
                                if p.text:
                                    reply = p.text.strip()
                                    if reply:
                                        logger.info(f"웹 검색 결과: {reply[:100]}")
                                        history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
                                        self._trim_history(channel_id)
                                        if len(reply) > 2000:
                                            reply = reply[:1997] + "..."
                                        return reply, file_to_send, _meta
                    except Exception as e:
                        logger.warning(f"웹 검색 실패: {e}")
                    break

                # 일반 도구 실행
                tool_args = dict(fc.args) if fc.args else {}
                if fc.name in ("search", "save_memory"):
                    tool_args["_user_id"] = user_id
                tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                tool_data = json.loads(tool_result)
                logger.info(f"도구 결과: {tool_result[:200]}")
                _meta["tool_results"].append(tool_result[:200])

                # send_file 액션
                if tool_data.get("_action") == "send_file":
                    save_path = os.path.join(FILES_DIR, tool_data["stored_name"])
                    if os.path.exists(save_path):
                        file_to_send = discord.File(save_path, filename=tool_data["filename"])
                        await self.library_db.increment_download(tool_data["file_id"])

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
                except Exception:
                    self.chat_histories[channel_id] = []
                    raise

            # 최종 응답 추출
            if not response.candidates or not response.candidates[0].content.parts:
                logger.warning("Gemini 안전 필터에 의해 응답 차단됨")
                _meta["error"] = "safety_filter"
                return "", None, _meta

            reply = self._extract_reply(response)

            # 반복 체크: 직전 봇 답변과 동일하면 temperature 올려서 1회 재시도
            if reply and self._is_repeat(history, reply):
                logger.warning(f"반복 감지, 재시도 (temperature 1.0): {reply[:50]}...")
                retry_config = types.GenerateContentConfig(
                    system_instruction=config.system_instruction,
                    tools=library_tools,
                    max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                    temperature=1.0,
                )
                try:
                    retry_response = self._call_gemini(history, retry_config)
                    retry_reply = self._extract_reply(retry_response)
                    if retry_reply:
                        reply = retry_reply
                except Exception as e:
                    logger.warning(f"재시도 실패: {e}")

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
                    return self.persona.daily_limit_message, None, _meta
                else:
                    logger.warning("분당 한도 초과")
                    _meta["error"] = "rate_limit"
                    return self.persona.rate_limit_message, None, _meta
            _meta["error"] = f"client_error:{e.status}"
            return self.persona.error_message, None, _meta

        except Exception as e:
            self.chat_histories[channel_id] = []
            logger.error(f"Gemini 에러: {type(e).__name__}: {e}")
            _meta["error"] = f"{type(e).__name__}"
            return self.persona.error_message, None, _meta

    def _call_gemini(self, contents, config):
        """살아있는 키로 호출, 실패 시 다음 키로 재시도"""
        last_err = None
        max_attempts = len(self._gemini_clients) * 2
        for _ in range(max_attempts):
            today = date.today()
            self._dead_until = {k: v for k, v in self._dead_until.items() if v > today}
            idx = self._client_index
            self._client_index = (self._client_index + 1) % len(self._gemini_clients)
            if idx in self._dead_until:
                continue
            client = self._gemini_clients[idx]
            try:
                return client.models.generate_content(
                    model=MODEL, contents=contents, config=config,
                )
            except ClientError as e:
                if e.status == "INVALID_ARGUMENT":
                    raise
                if e.status == "RESOURCE_EXHAUSTED" and "PerDay" in str(e):
                    self._dead_until[idx] = date.today() + timedelta(days=1)
                    logger.warning(f"키 #{idx} 일일 한도 초과, 내일까지 비활성화")
                else:
                    logger.warning(f"키 #{idx} 에러({type(e).__name__}), 다음 키로 재시도...")
                last_err = e
                continue
            except Exception as e:
                logger.warning(f"키 #{idx} 에러({type(e).__name__}), 다음 키로 재시도...")
                last_err = e
                continue
        if last_err:
            raise last_err
        raise ClientError("모든 API 키 소진")

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
    def _is_repeat(history: list, reply: str) -> bool:
        """히스토리 내 봇 답변 중 동일한 게 있는지 확인"""
        curr = reply.replace("\ufe0f", "").strip()
        for h in history:
            if h.role == "model" and h.parts and h.parts[0].text:
                prev = h.parts[0].text.replace("\ufe0f", "").strip()
                if prev == curr:
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
            line = f"책장 #{b['id']}: {b['title']}{author}{alias}"
            files = detail.get("files", [])
            if files:
                for f in files:
                    size_mb = f["file_size"] / (1024 * 1024)
                    line += f"\n  file:{f['id']} {f['filename']} ({size_mb:.1f}MB)"
            else:
                line += "\n  (파일 없음)"
            lines.append(line)
        return "\n".join(lines)

    async def _build_memories(self) -> str:
        """기억(learned)을 프롬프트용 텍스트로"""
        import aiosqlite
        from config import LIBRARIAN_DB_PATH
        async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT author, content FROM learned ORDER BY id")
            rows = await cursor.fetchall()
        if not rows:
            return "(기억 없음)"
        lines = []
        for r in rows:
            if r["author"]:
                lines.append(f"- {r['author']}: {r['content']}")
            else:
                lines.append(f"- {r['content']}")
        return "\n".join(lines)

    def _trim_history(self, channel_id: int):
        """히스토리를 MAX_HISTORY 턴으로 제한"""
        history = self.chat_histories.get(channel_id)
        if history and len(history) > MAX_HISTORY:
            self.chat_histories[channel_id] = history[-MAX_HISTORY:]
