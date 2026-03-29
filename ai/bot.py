"""
AI 사서봇 - Gemini function calling으로 도서관 기능 + 잡담
"""

import os
import re
import json
import asyncio
import discord
import logging
from collections import deque
from datetime import date, timedelta
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from library_db import LibraryDB
from librarian_db import LibrarianDB
from config import UPLOAD_DIR, ADMIN_IDS, LIGHTNING_ADDRESS
from ai.persona import Persona
from ai.tools import library_tools, execute_tool

logger = logging.getLogger("AILibrarian")

MODEL = "gemini-2.5-flash-lite"
BUFFER_SIZE = 30


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
        self._dead_until: dict[int, date] = {}  # client index -> 일일 한도 만료일
        self.chat_histories: dict[int, list] = {}  # channel_id -> Gemini 대화 턴
        self.channel_buffers: dict[int, deque] = {}  # channel_id -> 최근 메시지 버퍼
        self.global_buffer: deque = deque(maxlen=BUFFER_SIZE)  # 전체 채널 통합 버퍼
        self._channel_locks: dict[int, asyncio.Lock] = {}  # 채널별 동시 요청 방지
        self._ready = False

        # 기억 트리거 로드
        ai_dir = os.path.join(os.path.dirname(__file__), "ai")
        self._memory_triggers = self._load_patterns(os.path.join(ai_dir, "memory_triggers.txt"), ["기억해"])

        # 에러 메시지 목록 (히스토리 필터용)
        self._error_messages = set(
            persona._error_messages + persona._rate_limit_messages + persona._daily_limit_messages
        )

    @staticmethod
    def _load_patterns(path: str, default: list[str]) -> list[str]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]
        return default

    def _get_buffer(self, channel_id: int) -> deque:
        if channel_id not in self.channel_buffers:
            self.channel_buffers[channel_id] = deque(maxlen=BUFFER_SIZE)
        return self.channel_buffers[channel_id]

    def _add_to_buffer(self, channel_id: int, channel_name: str,
                       user_id: str, user_name: str,
                       text: str, is_bot: bool = False):
        entry = {
            "channel_name": channel_name,
            "user_id": user_id,
            "user_name": user_name,
            "text": text,
            "is_bot": is_bot,
        }
        self._get_buffer(channel_id).append(entry)
        self.global_buffer.append(entry)

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

    async def _fetch_channel_history(self, channel, limit: int) -> list[str]:
        """디스코드 API로 채널 히스토리를 가져와서 텍스트로 변환 (답글 구조 포함)"""
        messages = []
        async for msg in channel.history(limit=limit):
            messages.append(msg)
        messages.reverse()
        lines = []
        last_line = ""
        for msg in messages:
            name = self.persona.name if (self.user and msg.author.id == self.user.id) else msg.author.display_name
            text = msg.content
            extras = self._extract_extras(msg)
            if extras:
                text = f"{text} {extras}" if text else extras
            for user in msg.mentions:
                text = text.replace(f"<@{user.id}>", f"@{user.display_name}")
                text = text.replace(f"<@!{user.id}>", f"@{user.display_name}")
            # 답글 여부
            ref_msg = None
            if msg.reference:
                ref_msg = msg.reference.resolved
                if not ref_msg and msg.reference.message_id:
                    try:
                        ref_msg = await channel.fetch_message(msg.reference.message_id)
                    except Exception:
                        pass
            if ref_msg:
                ref_name = self.persona.name if (self.user and ref_msg.author.id == self.user.id) else ref_msg.author.display_name
                ref_text = ref_msg.content[:50]
                ref_extras = self._extract_extras(ref_msg)
                if ref_extras:
                    ref_text = f"{ref_text} {ref_extras}" if ref_text else ref_extras
                for u in ref_msg.mentions:
                    ref_text = ref_text.replace(f"<@{u.id}>", f"@{u.display_name}")
                    ref_text = ref_text.replace(f"<@!{u.id}>", f"@{u.display_name}")
                line = f"{name} [원본: {ref_name}이 쓴 \"{ref_text}\"]: {text}"
            else:
                line = f"{name}: {text}"
            # 중복 제거 + 봇 에러 메시지 제거
            is_error = (name == self.persona.name and text in self._error_messages)
            if line != last_line and not is_error:
                lines.append(line)
                last_line = line
        return lines

    async def _build_context(self, channel_id: int, user_id: str, guild) -> dict:
        """현재 채널은 API로, 다른 채널은 버퍼로 (현재 채널은 버퍼에서 제외)"""
        # 1. 현재 채널 - API에서 직접
        current_channel = self.get_channel(channel_id)
        ch_lines = await self._fetch_channel_history(current_channel, BUFFER_SIZE) if current_channel else []

        # 2. 서버 전체 - 버퍼 (현재 채널 제외)
        current_ch_name = getattr(current_channel, "name", "") if current_channel else ""
        global_lines = []
        for msg in self.global_buffer:
            if msg["channel_name"] == current_ch_name:
                continue
            name = self.persona.name if msg["is_bot"] else msg["user_name"]
            global_lines.append(f"[#{msg['channel_name']}] {name}: {msg['text']}")
        global_lines = global_lines[-BUFFER_SIZE:]

        # 3. 유저 최근 발언 - 현재 채널 히스토리에서 필터
        user = self.get_user(int(user_id))
        user_name = user.display_name if user else user_id
        user_lines = [l for l in ch_lines if l.startswith(f"{user_name}")][-5:]

        return {
            "channel": "\n".join(ch_lines),
            "global": "\n".join(global_lines),
            "user": "\n".join(user_lines),
        }

    @staticmethod
    def _clean_reply(text: str) -> str:
        """응답에서 쓰레기 데이터 정리"""
        lines = text.split("\n")
        cleaned = []
        for l in lines:
            s = l.strip()
            if s.startswith("{") or s.startswith("<"):
                continue
            if s.startswith("[") and not s.startswith("[원본:"):
                continue
            if "(" in s and ")" in s and any(kw in s for kw in ["print(", "search(", "import ", "def ", "await ", "return "]):
                continue
            cleaned.append(l)
        text = "\n".join(cleaned).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        # 잔여 멘션 태그 제거
        text = re.sub(r"<@!?\d+>", "", text).strip()
        # 중간 응답만으로 끝나는 경우 빈 응답 처리
        empty_patterns = ["검색해볼게", "찾아볼게", "기다려", "잠깐만", "보여줄게", "알려줄게"]
        if text and any(text.rstrip("!.⚡️⚡ ").endswith(p) for p in empty_patterns):
            return ""
        # 내부 도구 이름 노출 방지
        tool_names = ["list_entries", "search_entries", "get_entry_detail", "send_file",
                      "save_memory", "add_knowledge", "add_entry_alias", "add_alias",
                      "web_search", "search(", "recall_"]
        if text and any(tn in text for tn in tool_names):
            return ""
        return text

    async def _web_search(self, query: str, prompt: str, past_replies: set = None) -> str:
        """Google Search로 웹 검색 후 정리된 답변 반환"""
        # 답글 맥락 제거
        if "[원본:" in query:
            idx = query.find("]")
            if idx != -1:
                query = query[idx + 1:].strip()
        from ai.tools import google_search_tool
        web_history = [types.Content(role="user", parts=[types.Part.from_text(text=query)])]
        web_config = types.GenerateContentConfig(
            system_instruction=prompt,
            tools=google_search_tool,
            max_output_tokens=500,
            temperature=1.0,
        )
        # 멀티 키 시도
        for _ in range(len(self._gemini_clients) * 2):
            today = date.today()
            self._dead_until = {k: v for k, v in self._dead_until.items() if v > today}
            idx = self._client_index
            self._client_index = (self._client_index + 1) % len(self._gemini_clients)
            if idx in self._dead_until:
                continue
            client = self._gemini_clients[idx]
            try:
                response = client.models.generate_content(
                    model=MODEL, contents=web_history, config=web_config)
                if response.candidates and response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if part.text:
                            logger.info(f"웹 검색 원문: {part.text[:100]}")
                            cleaned = self._clean_reply(part.text)
                            if cleaned:
                                norm = cleaned.replace("\ufe0f", "").strip()
                                if past_replies and norm in past_replies:
                                    logger.warning(f"웹 검색 반복: {cleaned[:50]}...")
                                    return ""
                                # 웹 검색 결과를 지식으로 저장
                                try:
                                    await self.librarian_db.save(f"[웹검색] {query}: {cleaned[:200]}")
                                    logger.info(f"웹 검색 지식 저장: {query}")
                                except Exception:
                                    pass
                                return cleaned
                return ""
            except ClientError as e:
                if e.status == "RESOURCE_EXHAUSTED" and "PerDay" in str(e):
                    self._dead_until[idx] = date.today() + timedelta(days=1)
                    logger.warning(f"키 #{idx} 일일 한도 초과, 내일까지 비활성화")
                continue
            except Exception:
                continue
        return ""

    async def on_ready(self):
        await self.library_db.init()
        await self.librarian_db.init()
        knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge")
        await self.librarian_db.load_knowledge_from_files(knowledge_dir)
        await self.librarian_db.cleanup_learned()
        logger.info(f"{self.user} 온라인! ({self.persona.name})")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=self.persona.status_text
        ))
        self._ready = True

    async def on_message(self, message: discord.Message):
        # ready 전 메시지 무시 (밀린 메시지 방지)
        if not self._ready:
            return

        # 다른 봇 메시지 무시 (자신은 버퍼에 저장)
        channel_name = getattr(message.channel, "name", "DM")

        if message.author.bot:
            if self.user and message.author.id == self.user.id:
                self._add_to_buffer(
                    message.channel.id, channel_name,
                    str(message.author.id),
                    self.persona.name,
                    message.content,
                    is_bot=True,
                )
            return

        # 모든 유저 메시지를 버퍼에 저장 (멘션 태그를 이름으로 치환)
        text = message.content
        if message.mentions:
            for user in message.mentions:
                text = text.replace(f"<@{user.id}>", f"@{user.display_name}")
                text = text.replace(f"<@!{user.id}>", f"@{user.display_name}")
        self._add_to_buffer(
            message.channel.id, channel_name,
            str(message.author.id),
            message.author.display_name,
            text,
        )

        # 멘션 체크 (유저 멘션 또는 역할 멘션)
        bot_mentioned = self.user and self.user in message.mentions
        role_mentioned = False
        if not bot_mentioned and self.user and message.guild:
            bot_member = message.guild.get_member(self.user.id)
            if bot_member:
                role_mentioned = any(role in message.role_mentions for role in bot_member.roles if role.name != "@everyone")

        if not bot_mentioned and not role_mentioned:
            return

        # 멘션 제거해서 실제 메시지 추출
        for mention in [f"<@{self.user.id}>", f"<@!{self.user.id}>"]:
            text = text.replace(mention, "")
        # 역할 멘션 태그도 제거
        for role in message.role_mentions:
            text = text.replace(f"<@&{role.id}>", "")
        text = text.strip()

        # 멘션 메시지의 첨부/임베드 정보 추가
        msg_extras = self._extract_extras(message)
        if msg_extras:
            text = f"{text} {msg_extras}" if text else msg_extras

        # 빈 멘션이면 빈 문자열 그대로 전달 (프롬프트에서 처리)
        if not text:
            text = ""

        # 답글이면 원본 메시지 맥락 추가
        if message.reference:
            ref_msg = message.reference.resolved
            if not ref_msg and message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except Exception:
                    pass
            if ref_msg:
                ref_content = ref_msg.content[:100]
                # 멘션 태그를 이름으로 치환
                for u in ref_msg.mentions:
                    ref_content = ref_content.replace(f"<@{u.id}>", f"@{u.display_name}")
                    ref_content = ref_content.replace(f"<@!{u.id}>", f"@{u.display_name}")
                ref_extras = self._extract_extras(ref_msg)
                if ref_extras:
                    ref_content = f"{ref_content} {ref_extras}" if ref_content else ref_extras
                # 에러 메시지에 답글한 경우 맥락 제거
                if ref_msg.content not in self._error_messages:
                    ref_name = self.persona.name if (self.user and ref_msg.author.id == self.user.id) else ref_msg.author.display_name
                    text = f"[원본: {ref_name}이 쓴 \"{ref_content}\"] {text}"

        # 웹 검색 플래그
        web_keywords = ["검색해", "구글링해", "웹검색", "구글 검색",
                        "조사해", "알아봐",
                        "뉴스 알려", "소식 알려", "시세 알려", "날씨 알려"]
        text_normalized = " ".join(text.split())  # 공백 정규화
        use_web = any(kw in text_normalized for kw in web_keywords)

        # 채널별 락으로 동시 요청 방지
        ch_id = message.channel.id
        if ch_id not in self._channel_locks:
            self._channel_locks[ch_id] = asyncio.Lock()

        async with self._channel_locks[ch_id]:
            async with message.channel.typing():
                reply_text, file_to_send, ai_saved = await self._ask_gemini(
                    channel_id=ch_id,
                    user_id=str(message.author.id),
                    user_name=message.author.display_name,
                    user_text=text,
                    guild=message.guild,
                    use_web=use_web,
                )

        # 기억 트리거 감지 (AI가 이미 저장했으면 건너뜀)
        if text and not ai_saved:
            clean_text = text
            if self.user:
                for tag in [f"@{self.persona.name}", f"<@{self.user.id}>", f"<@!{self.user.id}>"]:
                    clean_text = clean_text.replace(tag, "").strip()
            text_lower = text.lower()
            display = message.author.display_name
            uid = str(message.author.id)

            # 질문 판별
            has_question = "?" in text
            confirm_patterns = ["알았어", "알았지", "알겠어", "알겠지", "알겠니", "알겠냐"]
            is_question = has_question and not any(cp in text_lower for cp in confirm_patterns)

            # 기억 트리거
            if not is_question and any(kw in text_lower for kw in self._memory_triggers):
                await self.librarian_db.save(f"{display}: {clean_text}")
                logger.info(f"학습 저장: {clean_text}")

            # 설명식 패턴 (트리거 없어도 저장)
            elif "?" not in clean_text and "뭐" not in clean_text and "누구" not in clean_text and "알아" not in clean_text and re.search(r'.+[은는이가]\s+.+(?:이야|이다|야|다|임)$', clean_text):
                await self.librarian_db.save(clean_text)
                logger.info(f"설명식 학습 저장: {clean_text}")

        # 빈 응답(안전 필터 차단 등)이면 무시
        if not reply_text and not file_to_send:
            logger.warning("모든 시도 실패 - 에러 메시지 출력")
            reply_text = self.persona.error_message

        # 응답 로그
        guild_name = message.guild.name if message.guild else "DM"
        channel_name = getattr(message.channel, "name", "DM")
        logger.info(f"[{guild_name}/#{channel_name}] {message.author.display_name}(ID:{message.author.id}): {text}")
        logger.info(f"[{guild_name}/#{channel_name}] {self.persona.name}: {reply_text}")

        if file_to_send:
            await message.reply(reply_text, file=file_to_send)
        else:
            await message.reply(reply_text)

    async def _ask_gemini(self, channel_id: int, user_id: str,
                          user_name: str, user_text: str,
                          guild=None, use_web=False) -> tuple[str, discord.File | None, bool]:
        """Gemini에게 질문하고 응답 + 파일(있으면) + AI저장여부 반환"""
        if channel_id not in self.chat_histories:
            self.chat_histories[channel_id] = []
        history = self.chat_histories[channel_id]

        # 프롬프트 조립: 페르소나 → 도구 → 맥락 → 도구 → 페르소나
        parts = []

        # 1. 페르소나 (앞)
        parts.append(self.persona.persona_text)

        # 2. 도구/규칙 (앞)
        parts.append(self.persona.prompt_text)

        # 3. 맥락
        admin_names = []
        if guild:
            for aid in ADMIN_IDS:
                member = guild.get_member(int(aid))
                if member:
                    admin_names.append(member.display_name)
        role = "주인 (도서관 관리자)" if user_id in ADMIN_IDS else "일반 방문자"
        logger.info(f"대화 상대: {user_name} (ID: {user_id}) → {role}")

        from datetime import datetime as dt, timezone as tz
        import zoneinfo
        try:
            tz_name = os.getenv("TZ", "Asia/Seoul")
            now = dt.now(zoneinfo.ZoneInfo(tz_name))
        except Exception:
            now = dt.now()
        info_block = f"## 상황\n현재: {now.strftime('%Y년 %m월 %d일 %H:%M')}\n대화 상대: {user_name} ({role})"
        if admin_names:
            info_block += f"\n도서관 주인: {', '.join(admin_names)}"
        if LIGHTNING_ADDRESS:
            info_block += f"\n후원 라이트닝 주소: {LIGHTNING_ADDRESS}"
        parts.append(info_block)

        ctx = await self._build_context(channel_id, user_id, guild)
        if ctx["channel"]:
            parts.append(f"## 현재 채널 대화\n{ctx['channel']}")

        # 4. 도구 리마인드 (뒤)
        parts.append(self.persona.reminder_text)

        # 5. 페르소나 (뒤)
        parts.append(self.persona.persona_text)

        dynamic_prompt = "\n\n".join(p for p in parts if p)
        logger.info(f"프롬프트 길이: {len(dynamic_prompt)}자")

        # 유저 메시지 구성
        if user_text:
            user_content = f"{user_name}: {user_text}"
        else:
            user_content = f"({user_name}이 빈 멘션을 보냈다.)"

        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))

        file_to_send = None
        ai_saved = False
        history_snapshot = len(history)  # 롤백 지점

        def _gen_config():
            return types.GenerateContentConfig(
                system_instruction=dynamic_prompt,
                tools=library_tools,
                max_output_tokens=500,
                temperature=0.8,
            )

        def _next_client():
            """일일 한도가 안 죽은 다음 클라이언트 반환. 전부 죽으면 None."""
            today = date.today()
            # 만료일 지난 키 복구
            self._dead_until = {k: v for k, v in self._dead_until.items() if v > today}
            for _ in range(len(self._gemini_clients)):
                idx = self._client_index
                self._client_index = (self._client_index + 1) % len(self._gemini_clients)
                if idx not in self._dead_until:
                    return idx, self._gemini_clients[idx]
            return None, None

        def _call_gemini(contents):
            """살아있는 키로 호출, 실패 시 다음 키로 재시도 (최대 키 수 × 2)"""
            last_err = None
            max_attempts = len(self._gemini_clients) * 2
            for _ in range(max_attempts):
                idx, client = _next_client()
                if client is None:
                    break
                try:
                    return client.models.generate_content(
                        model=MODEL, contents=contents, config=_gen_config(),
                    )
                except ClientError as e:
                    if e.status == "INVALID_ARGUMENT":
                        # 400 에러는 요청 자체가 잘못됨 - 재시도 의미 없음
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

        try:
            # 웹 검색 모드
            # past_replies 구축 (모든 경로에서 사용)
            def _normalize(t):
                return t.replace("\ufe0f", "").strip()

            past_replies = set()
            for h in history:
                if h.role == "model" and h.parts and h.parts[0].text:
                    past_replies.add(_normalize(h.parts[0].text))
            bot_name = self.persona.name
            for line in (ctx.get("channel", "") or "").split("\n"):
                if line.startswith(f"{bot_name}: "):
                    past_replies.add(_normalize(line.split(": ", 1)[1]))
                elif line.startswith(f"{bot_name} ["):
                    idx = line.find("]: ")
                    if idx != -1:
                        past_replies.add(_normalize(line[idx + 3:]))

            if use_web:
                logger.info("웹 검색 모드")
                reply = await self._web_search(user_text, dynamic_prompt, past_replies)
                if not reply:
                    # 맥락 제거 후 재시도
                    logger.info("웹 검색 재시도 (맥락 제거)")
                    clean_parts = [p for p in parts if not p.startswith("## 현재 채널 대화")]
                    clean_prompt = "\n\n".join(p for p in clean_parts if p)
                    reply = await self._web_search(user_text, clean_prompt, past_replies)
                if reply:
                    history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
                    if len(history) > 6:
                        self.chat_histories[channel_id] = history[-6:]
                    if len(reply) > 2000:
                        reply = reply[:1997] + "..."
                    return reply, file_to_send, ai_saved
                return self.persona.error_message, None, False

            response = _call_gemini(history)

            # function call 루프 (최대 5회 - 기억 조회+저장+도서관 조합)
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
                if fc.name in ("save_memory", "add_knowledge"):
                    ai_saved = True

                # AI가 웹 검색이 필요하다고 판단
                if fc.name == "web_search":
                    query = (dict(fc.args) if fc.args else {}).get("query", user_text)
                    logger.info(f"AI 판단 웹 검색: {query}")
                    reply = await self._web_search(query, dynamic_prompt, past_replies)
                    if reply:
                        history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
                        if len(history) > 6:
                            self.chat_histories[channel_id] = history[-6:]
                        if len(reply) > 2000:
                            reply = reply[:1997] + "..."
                        return reply, file_to_send, ai_saved
                    break

                tool_args = dict(fc.args) if fc.args else {}
                if fc.name in ("search", "save_memory"):
                    tool_args["_user_id"] = user_id
                tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                tool_data = json.loads(tool_result)
                logger.info(f"도구 결과: {tool_result[:200]}")

                # search 결과 없으면 그대로 Gemini에게 돌려줌 (없다고 답하게)

                # send_file 액션: 실제 파일 전송 준비
                if tool_data.get("_action") == "send_file":
                    save_path = os.path.join(UPLOAD_DIR, tool_data["stored_name"])
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
                    response = _call_gemini(history)
                except Exception:
                    # API 실패 시 히스토리 전체 폐기
                    self.chat_histories[channel_id] = []
                    raise

            # 안전 필터 차단 체크
            if not response.candidates or not response.candidates[0].content.parts:
                logger.warning("Gemini 안전 필터에 의해 응답 차단됨")
                return "", None

            # 모든 텍스트 parts를 정리해서 합침
            reply_parts = []
            for part in response.candidates[0].content.parts:
                if part.text:
                    logger.info(f"원문: {part.text[:100]}")
                    cleaned = self._clean_reply(part.text)
                    if cleaned:
                        reply_parts.append(cleaned)
            reply = "\n".join(reply_parts) if reply_parts else ""

            # 반복 방지
            norm_reply = _normalize(reply) if reply else ""
            logger.info(f"반복 비교: reply={norm_reply[:50] if norm_reply else '없음'}... past={len(past_replies)}개")
            if norm_reply and norm_reply in past_replies:
                logger.warning("직전 답변과 동일 - 채널 대화 없이 재시도")
                # 히스토리 롤백
                self.chat_histories[channel_id] = []
                # 채널 대화 없는 프롬프트로 재구성
                clean_parts = [p for p in parts if not p.startswith("## 현재 채널 대화")]
                clean_prompt = "\n\n".join(p for p in clean_parts if p)
                def _clean_config():
                    return types.GenerateContentConfig(
                        system_instruction=clean_prompt,
                        tools=library_tools,
                        max_output_tokens=500,
                        temperature=0.9,
                    )
                try:
                    # 히스토리 없이 유저 메시지만으로 재시도
                    clean_history = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

                    def _retry_call(contents):
                        last_err = None
                        for _ in range(len(self._gemini_clients) * 2):
                            ri, rc = _next_client()
                            if rc is None:
                                break
                            try:
                                return rc.models.generate_content(
                                    model=MODEL, contents=contents, config=_clean_config())
                            except Exception as e:
                                last_err = e
                                continue
                        if last_err:
                            raise last_err

                    response = _retry_call(clean_history)
                    if response:
                        # 도구 호출 루프
                        for _ in range(5):
                            if not response.candidates or not response.candidates[0].content.parts:
                                break
                            fc = None
                            for part in response.candidates[0].content.parts:
                                    fc = part.function_call
                                    break
                            if not fc:
                                break
                            logger.info(f"도구 호출 (재시도): {fc.name}({fc.args})")

                            # 재시도에서도 web_search 처리
                            if fc.name == "web_search":
                                query = (dict(fc.args) if fc.args else {}).get("query", user_text)
                                logger.info(f"재시도 웹 검색: {query}")
                                reply = await self._web_search(query, clean_prompt, past_replies)
                                if reply:
                                    return reply, file_to_send, ai_saved
                                break

                            tool_args = dict(fc.args) if fc.args else {}
                            if fc.name in ("search", "save_memory"):
                                tool_args["_user_id"] = user_id
                            tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                            tool_data = json.loads(tool_result)
                            logger.info(f"도구 결과: {tool_result[:200]}")

                            clean_history.append(response.candidates[0].content)
                            clean_history.append(types.Content(role="user", parts=[types.Part.from_function_response(
                                name=fc.name, response=tool_data)]))
                            response = _retry_call(clean_history)
                        reply_parts = []
                        for part in response.candidates[0].content.parts:
                            if part.text:
                                cleaned = self._clean_reply(part.text)
                                if cleaned:
                                    reply_parts.append(cleaned)
                        reply = "\n".join(reply_parts) if reply_parts else ""
                        # 재시도에서도 같은 답이면 에러
                        if reply and _normalize(reply) in past_replies:
                            logger.warning(f"2차 재시도에서도 반복: {reply[:50]}...")
                            reply = ""
                except Exception as e:
                    logger.error(f"재시도 실패: {e}")
                    reply = ""

            if reply:
                history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
            else:
                if history and history[-1].role == "user":
                    history.pop()

            if len(history) > 6:
                self.chat_histories[channel_id] = history[-6:]

            if len(reply) > 2000:
                reply = reply[:1997] + "..."

            return reply, file_to_send, ai_saved

        except ClientError as e:
            logger.error(f"Gemini ClientError: status={e.status} code={getattr(e, 'code', '?')} message={e}")
            # 히스토리 롤백 (도구 호출 중 꼬인 것 복구)
            self.chat_histories[channel_id] = []
            if e.status == "RESOURCE_EXHAUSTED":
                msg = str(e)
                if "PerDay" in msg or "per_day" in msg:
                    logger.warning("일일 한도 초과 (모든 키 소진)")
                    return self.persona.daily_limit_message, None, False
                else:
                    logger.warning("분당 한도 초과")
                    return self.persona.rate_limit_message, None, False
            return self.persona.error_message, None, False

        except Exception as e:
            self.chat_histories[channel_id] = []
            logger.error(f"Gemini 에러: {type(e).__name__}: {e}")
            return self.persona.error_message, None, False
