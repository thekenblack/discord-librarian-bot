"""
AI 사서봇 - Gemini function calling으로 도서관 기능 + 잡담
"""

import os
import aiosqlite
import re
import asyncio
import discord
import logging
from google import genai
from google.genai import types
from google.genai.errors import ClientError

from library.db import LibraryDB
from librarian.db import LibrarianDB
from config import ADMIN_IDS, LIGHTNING_ADDRESS, GEMINI_MODEL, AI_MAX_OUTPUT_TOKENS, LOG_DIR
from librarian import server_log
import importlib as _il
_persona_mod = _il.import_module("librarian.layers.03_character.persona")
_tools_mod = _il.import_module("librarian.layers.02_functioning.tools")
_btc_mod = _il.import_module("librarian.layers.02_functioning.bitcoin_data")
Persona = _persona_mod.Persona
parse_url = _tools_mod.parse_url
bitcoin_data = _btc_mod

logger = logging.getLogger("AILibrarian")

MODEL = GEMINI_MODEL
MAX_HISTORY = 5               # L3 유저별
MAX_PERCEPTION_HISTORY = 5    # L1 채널별
MAX_EVALUATION_HISTORY = 5    # L5 단일

# 커스텀 이모지 <:name:id> 또는 유니코드 이모지 (ZWJ 시퀀스 포함) 개별 추출
_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
_UNICODE_EMOJI_RE = re.compile(
    r"[\U0001F1E0-\U0001F1FF]{2}"          # 국기 이모지
    r"|(?:[\U0001F600-\U0001FAFF]"          # 이모지 본체
    r"  (?:\uFE0F)?"                        # variation selector
    r"  (?:\u200D"                           # ZWJ 시퀀스
    r"    [\U0001F600-\U0001FAFF\u2600-\u27BF]"
    r"    (?:\uFE0F)?"
    r"  )*"
    r")"
    r"|[\u2600-\u27BF]\uFE0F?"              # 기호 이모지
    r"|[\u231A-\u23F3]\uFE0F?"              # 시계 등 기호
    r"|[\u2702-\u27B0]\uFE0F?"              # 가위 등 기호
, re.VERBOSE)


def _extract_emojis(raw: str) -> list[str]:
    """reaction 문자열에서 유효한 이모지만 개별 추출."""
    if not raw:
        return []
    custom = _CUSTOM_EMOJI_RE.findall(raw)
    if custom:
        return custom
    return _UNICODE_EMOJI_RE.findall(raw)


class AILibrarianBot(discord.Client):
    def __init__(self, persona: Persona, gemini_api_key: str):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.persona = persona
        self.library_db = LibraryDB()
        self.librarian_db = LibrarianDB()
        self._gemini_client = genai.Client(api_key=gemini_api_key)
        self.chat_histories: dict[str, list] = {}  # user_id → history (L3)
        self.perception_histories: dict[str, list] = {}  # channel_id → history (L1)
        self.evaluation_history: list = []  # 단일 히스토리 (L5)
        self._evaluation_queue: asyncio.Queue = asyncio.Queue()  # L5 작업 큐
        self._evaluation_task: asyncio.Task | None = None  # L5 워커 태스크
        self._user_locks: dict[str, asyncio.Lock] = {}  # user_id → lock
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

        # 벡터 스토어 초기화 + 동기화
        try:
            from librarian.vector_store import VectorStore
            from config import CHROMA_DIR
            self.librarian_db.vector_store = VectorStore(CHROMA_DIR)
            await self.librarian_db.sync_vector_store()
            logger.info("벡터 스토어 초기화 완료")
        except Exception as e:
            logger.warning(f"벡터 스토어 초기화 실패 (LIKE 검색으로 동작): {e}")
            self.librarian_db.vector_store = None

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

        status_name = self.persona.status_text
        if LIGHTNING_ADDRESS:
            status_name = f"⚡ {LIGHTNING_ADDRESS}"
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=status_name
        ))
        asyncio.create_task(bitcoin_data.start_background_update())
        asyncio.create_task(self._learn_all_books())
        self._bot_ready = True
        self._evaluation_task = asyncio.create_task(self._evaluation_worker())

    async def _evaluation_worker(self):
        """L5 큐 워커. 큐에서 하나씩 꺼내서 순서대로 처리."""
        while True:
            try:
                kwargs = await self._evaluation_queue.get()
                await self._run_evaluation(**kwargs)
            except Exception as e:
                logger.warning(f"[Evaluation Worker] 에러 (무시): {e}")
            finally:
                self._evaluation_queue.task_done()

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

    async def _learn_all_books(self):
        """미학습 도서 일괄 학습"""
        _bl = _il.import_module("librarian.layers.02_functioning.book_learning")
        learn_book = _bl.learn_book
        try:
            books = await self.library_db.list_all_books()
            for book in books:
                detail = await self.library_db.get_book_detail(book["id"])
                for f in detail.get("files", []):
                    await learn_book(self.librarian_db, book["id"], book["title"], f["filename"], f["stored_name"])
        except Exception as e:
            logger.error(f"도서 일괄 학습 실패: {e}")

    # on_raw_reaction_add 제거: 자동 이모지 따라누르기 삭제
    # 이모지 리액션은 Character(L3)가 판단해서 직접 결정

    async def on_message(self, message: discord.Message):
        if not self._bot_ready:
            return

        channel_name = getattr(message.channel, "name", "DM")
        guild_name = message.guild.name if message.guild else "DM"

        # 모든 메시지 DB 저장 (맥락 수집용)
        try:
            ref_id = None
            if message.reference and message.reference.message_id:
                ref_id = str(message.reference.message_id)
            await self.librarian_db.save_message(
                message_id=str(message.id),
                channel_id=str(message.channel.id),
                author_id=str(message.author.id),
                author_name=message.author.display_name,
                content=message.content or "",
                reference_id=ref_id,
                is_bot=message.author.bot,
            )
        except Exception:
            pass  # 저장 실패해도 대화 진행에 영향 없음

        if message.author.bot:
            if self.user and message.author.id == self.user.id:
                server_log.log(guild=guild_name, channel=channel_name,
                               author=self.persona.name, content=message.content, is_bot=True)
            else:
                # 선물 메시지 감지 (라이브러리 봇 → [GIFT] 마커)
                await self._check_gift_message(message)
            return

        text = message.content
        if message.mentions:
            for user in message.mentions:
                text = text.replace(f"<@{user.id}>", f"@{user.display_name}")
                text = text.replace(f"<@!{user.id}>", f"@{user.display_name}")

        server_log.log(guild=guild_name, channel=channel_name,
                       author=message.author.display_name, content=text)

        # 멘션 체크 (직접 멘션 + 역할 멘션 + 봇 메시지에 답글)
        bot_mentioned = self.user and self.user in message.mentions
        reply_to_bot = False
        if not bot_mentioned and self.user and message.reference:
            ref = message.reference.resolved
            if ref and ref.author.id == self.user.id:
                reply_to_bot = True
        role_mentioned = False
        if not bot_mentioned and not reply_to_bot and self.user and message.guild:
            bot_member = message.guild.get_member(self.user.id)
            if bot_member:
                role_mentioned = any(role in message.role_mentions for role in bot_member.roles if role.name != "@everyone")

        if not bot_mentioned and not reply_to_bot and not role_mentioned:
            return

        import time as _time
        _t0 = _time.monotonic()
        logger.info(f"[수신] {message.author.display_name}(#{getattr(message.channel, 'name', 'DM')}): {message.content[:100]}")

        # 멘션 태그 제거
        for mention in [f"<@{self.user.id}>", f"<@!{self.user.id}>"]:
            text = text.replace(mention, "")
        for role in message.role_mentions:
            text = text.replace(f"<@&{role.id}>", "")
        text = text.strip()

        # 첨부/임베드 정보 추가
        _t1 = _time.monotonic()
        msg_extras = await self._extract_extras(message)
        logger.info(f"[타이밍] extract_extras: {_time.monotonic()-_t1:.2f}s")
        if msg_extras:
            text = f"{text} {msg_extras}" if text else msg_extras

        if not text:
            text = ""

        # 맥락 수집 (DB 우선, API 폴백)
        _t2 = _time.monotonic()
        reply_chain, seen_filenames, chain_attachments, anchor_id = await self._build_reply_chain(message)
        anchor_context, recent_context = await self._build_context_messages(message, anchor_id=anchor_id)
        logger.info(f"[타이밍] 맥락수집: {_time.monotonic()-_t2:.2f}s (reply={len(reply_chain)} anchor={len(anchor_context)} recent={len(recent_context)})")
        logger.info(f"[타이밍] 전처리 총: {_time.monotonic()-_t0:.2f}s")

        # 첨부파일: 현재 메시지 + 답글 체인의 첨부파일
        all_attachments = list(message.attachments) + chain_attachments

        uid = str(message.author.id)
        if uid not in self._user_locks:
            self._user_locks[uid] = asyncio.Lock()

        async with self._user_locks[uid]:
            try:
                await message.channel.typing()
            except Exception:
                pass
            reply_text, files_to_send, _meta = await self._ask_gemini(
                    user_id=uid,
                    user_name=message.author.display_name,
                    user_text=text,
                    guild=message.guild,
                    reply_chain=reply_chain,
                    anchor_context=anchor_context,
                    recent_context=recent_context,
                    attachments=all_attachments,
                    seen_filenames=seen_filenames,
                    channel_id=str(message.channel.id),
                )

        if not reply_text and not files_to_send:
            # 이모지 리액션
            if _meta.get("reaction"):
                for em in _extract_emojis(_meta["reaction"]):
                    try:
                        await message.add_reaction(em)
                        logger.info(f"이모지 리액션: {em}")
                    except Exception as e:
                        logger.warning(f"리액션 실패: {e}")
                return
            if _meta.get("intentional_silence"):
                logger.info("의도적 무응답 → 메시지 안 보냄")
                return
            # 에러 메시지 (비어있으면 무응답)
            error_msg = self.persona.error_message
            if not error_msg:
                return
            reply_text = error_msg

        # 에러 발생 시 어드민 알림
        if _meta.get("error"):
            error_type = _meta.get("error") or "unknown"
            channel_name_short = getattr(message.channel, "name", "DM")
            self._queue_admin_notify(
                f"에러 `{error_type}` — {message.author.display_name}(#{channel_name_short}): {message.content[:80]}"
            )

        # 빈 에러 메시지면 무응답
        if not reply_text and not files_to_send:
            logger.info(f"[{guild_name}/#{channel_name}] 무응답 처리 (에러)")
            return

        logger.info(f"[{guild_name}/#{channel_name}] {message.author.display_name}(ID:{message.author.id}): {text}")
        logger.info(f"[{guild_name}/#{channel_name}] {self.persona.name}: {reply_text}")

        async def _send_reply(text, file=None, files=None, embed=None):
            """reply 실패 시 무시 (원본 삭제됨)"""
            try:
                if files:
                    await message.reply(text or "", files=files)
                elif file:
                    await message.reply(text, file=file)
                elif embed:
                    if text:
                        await message.reply(text, embed=embed)
                    else:
                        await message.reply(embed=embed)
                else:
                    await message.reply(text)
            except discord.HTTPException:
                logger.info("reply 실패 (원본 삭제) → 무시")

        # share_url: AI 응답에 URL이 누락됐으면 끝에 추가
        if _meta.get("shared_urls"):
            for _surl in _meta["shared_urls"]:
                if _surl not in (reply_text or ""):
                    reply_text = f"{reply_text}\n{_surl}".strip() if reply_text else _surl

        if files_to_send:
            await _send_reply(reply_text, files=files_to_send)
        elif reply_text:
            import re as _re
            # 커스텀 이모지: 현재 서버에 없으면 제거
            if message.guild:
                guild_emoji_ids = {str(e.id) for e in message.guild.emojis}
                def _check_emoji(m):
                    return m.group() if m.group(2) in guild_emoji_ids else ""
                reply_text = _re.sub(r'<(a?:\w+:)(\d+)>', _check_emoji, reply_text).strip()
            # URL이 있으면 텍스트에서 빼고 임베드로 이미지만 표시
            _img_match = _re.search(r'(https?://\S+\.(?:gif|png|jpg|jpeg|webp)(?:\?\S*)?)', reply_text)
            _page_match = not _img_match and _re.search(r'(https?://(?:tenor\.com|giphy\.com)/\S+)', reply_text)

            if _img_match:
                # 이미지 파일 URL → 바로 임베드
                _url = _img_match.group()
                _text = reply_text.replace(_url, '').strip()
                _embed = discord.Embed()
                _embed.set_image(url=_url)
                await _send_reply(_text, embed=_embed)
            elif _page_match:
                # tenor/giphy HTML → og:image 추출 후 임베드
                _url = _page_match.group()
                _text = reply_text.replace(_url, '').strip()
                _media_url = None
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as _s:
                        async with _s.get(_url, timeout=aiohttp.ClientTimeout(total=5),
                                          headers={"User-Agent": "Mozilla/5.0"}) as _r:
                            if _r.status == 200:
                                _html = await _r.text(errors="replace")
                                _og = _re.search(
                                    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)', _html)
                                if not _og:
                                    _og = _re.search(
                                        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', _html)
                                if _og:
                                    _media_url = _og.group(1)
                except Exception:
                    pass
                if _media_url:
                    _embed = discord.Embed()
                    _embed.set_image(url=_media_url)
                    await _send_reply(_text, embed=_embed)
                else:
                    await _send_reply(reply_text)
            else:
                await _send_reply(reply_text)

        # 이모지 리액션
        if _meta.get("reaction"):
            for em in _extract_emojis(_meta["reaction"]):
                try:
                    await message.add_reaction(em)
                except Exception as e:
                    logger.warning(f"리액션 실패: {e}")

    async def _ask_gemini(self, user_id: str,
                          user_name: str, user_text: str,
                          guild=None, reply_chain: list[str] = None,
                          anchor_context: list[str] = None,
                          recent_context: list[str] = None,
                          attachments: list = None,
                          seen_filenames: list[str] = None,
                          channel_id: str = None) -> tuple[str, list, dict]:
        """v5 5레이어: Perception → Functioning → Character → Postprocess → Evaluation"""
        import time as _time
        import re as _re
        _meta = {"tools_called": [], "tool_results": [], "error": None}

        if user_id not in self.chat_histories:
            self.chat_histories[user_id] = []
        history = self.chat_histories[user_id]

        # L1 채널별 히스토리
        if channel_id and channel_id not in self.perception_histories:
            self.perception_histories[channel_id] = []

        try:
            # ── Layer 1: Perception (맥락 파악) ──
            _t0 = _time.monotonic()
            raw_context = await self._gather_context(
                user_id, user_name, guild, reply_chain,
                anchor_context=anchor_context, recent_context=recent_context,
                channel_id=channel_id)
            p_history = list(self.perception_histories.get(channel_id, [])) if channel_id else []
            perception = await self._run_perception(
                user_id, user_name, user_text, raw_context,
                history=p_history)
            # L1 히스토리에 이번 턴 추가 (asyncio 싱글 스레드라 락 불필요)
            if channel_id is not None:
                self.perception_histories[channel_id].append(types.Content(role="user", parts=[
                    types.Part.from_text(text=f"{user_name}: {user_text}" if user_text else f"({user_name}이 빈 멘션을 보냈다.)")]))
                self.perception_histories[channel_id].append(types.Content(role="model", parts=[
                    types.Part.from_text(text=perception)]))
                self._trim_perception_history(channel_id)
            logger.info(f"[L1 Perception] 완료 ({_time.monotonic()-_t0:.2f}s)")

            # ── Layer 2: Functioning (도구 실행) ──
            _t0 = _time.monotonic()
            catalog = await self._build_catalog()
            memories_text, memory_ids = await self._build_memories(user_name)

            instruction, files_to_send, processor_meta = await self._run_functioning(
                user_id=user_id, user_name=user_name, user_text=user_text,
                catalog=catalog, memories_text=memories_text,
                memory_ids=memory_ids,
                attachments=attachments, seen_filenames=seen_filenames,
                perception=perception,
            )
            _meta["tools_called"] = processor_meta.get("tools_called", [])
            _meta["tool_results"] = processor_meta.get("tool_results", [])
            if processor_meta.get("shared_urls"):
                _meta["shared_urls"] = processor_meta["shared_urls"]
            if processor_meta.get("reaction"):
                _meta["reaction"] = processor_meta["reaction"]
            logger.info(f"[L2 Functioning] 완료 ({_time.monotonic()-_t0:.2f}s)")

            # 응답 모드 판별
            if _re.search(r'(?:응답\s*모드\s*[:：]\s*)?무응답', instruction or ""):
                logger.info("[L2] 응답 모드: 무응답")
                _meta["intentional_silence"] = True
                return "", files_to_send, _meta

            reaction_only_match = _re.search(
                r'(?:응답\s*모드\s*[:：]\s*)?리액션만\s*[:：]?\s*(.+)', instruction or "")
            if reaction_only_match:
                emoji_str = reaction_only_match.group(1).strip()
                emojis = _extract_emojis(emoji_str)
                if emojis:
                    _meta["reaction"] = emoji_str
                    logger.info(f"[L2] 응답 모드: 리액션만 → {emoji_str}")
                    return "", files_to_send, _meta

            # 일반 응답에 리액션이 포함된 경우 파싱 ("리액션: 😊")
            if instruction:
                reaction_match = _re.search(r'리액션\s*[:：]\s*(.+?)$', instruction, _re.MULTILINE)
                if reaction_match:
                    emoji_str = reaction_match.group(1).strip()
                    emojis = _extract_emojis(emoji_str)
                    if emojis:
                        _meta["reaction"] = emoji_str
                        logger.info(f"[L2] 리액션 감지: {emoji_str}")
                    # 리액션 줄을 instruction에서 제거 (L3에 안 넘김)
                    instruction = _re.sub(r'\n?리액션\s*[:：]\s*.+?$', '', instruction, flags=_re.MULTILINE).strip()

            # ── Layer 3: Character (대사 생성) ──
            _t0 = _time.monotonic()

            if user_text:
                user_content = f"{user_name}: {user_text}"
            else:
                user_content = f"({user_name}이 빈 멘션을 보냈다.)"

            history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))

            raw_reply = await self._run_character(
                user_id=user_id, user_name=user_name,
                user_text=user_text, instruction=instruction,
                context_block=perception,
            )
            logger.info(f"[L3 Character] 완료 ({_time.monotonic()-_t0:.2f}s) | {raw_reply[:100] if raw_reply else '(빈 응답)'}")

            if not raw_reply:
                if history and history[-1].role == "user":
                    history.pop()
                _meta["intentional_silence"] = True
                return "", files_to_send, _meta

            # ── Layer 4: Postprocess (자연어 정제) ──
            _t0 = _time.monotonic()
            # 멘션 매핑 구성: reply_chain + pre_context에서 이름(<@id>) 패턴 추출
            mention_map = {}
            for line in (reply_chain or []) + (pre_context or []):
                for m in _re.finditer(r'(\S+?)\(<@(\d+)>\)', line):
                    mention_map[m.group(1)] = m.group(2)
            # 채널 매핑 구성: guild의 텍스트 채널 이름 → ID
            channel_map = {}
            if guild:
                for ch in guild.text_channels:
                    channel_map[ch.name] = str(ch.id)
            reply = await self._run_postprocess(
                raw_reply, user_name,
                mention_map=mention_map, channel_map=channel_map)
            if reply != raw_reply:
                logger.info(f"[L4 Postprocess] 정제 ({_time.monotonic()-_t0:.2f}s)")
            else:
                logger.info(f"[L4 Postprocess] 통과 ({_time.monotonic()-_t0:.2f}s)")

            # 히스토리에 최종 응답 추가
            history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))
            self._trim_history(user_id)

            # ── Layer 5: Evaluation (큐에 추가, 백그라운드 워커가 처리) ──
            if reply:
                self._evaluation_queue.put_nowait({
                    "user_id": user_id, "user_name": user_name,
                    "user_text": user_text, "bot_reply": reply,
                    "context": perception, "tool_results": instruction,
                    "channel_id": channel_id,
                })

            if len(reply) > 2000:
                reply = reply[:1997] + "..."

            return reply, files_to_send, _meta

        except ClientError as e:
            logger.error(f"Gemini ClientError: status={e.status} code={getattr(e, 'code', '?')} message={e}")
            self.chat_histories[user_id] = []
            if e.status == "RESOURCE_EXHAUSTED":
                msg = str(e)
                if "PerDay" in msg or "per_day" in msg:
                    logger.warning("일일 한도 초과 (모든 키 소진)")
                    _meta["error"] = "daily_limit"
                    return self.persona.error_message, [], _meta
                else:
                    logger.warning("분당 한도 초과")
                    _meta["error"] = "rate_limit"
                    return self.persona.error_message, [], _meta
            if e.status == "INVALID_ARGUMENT":
                logger.warning("INVALID_ARGUMENT → 히스토리 초기화 후 클린 재시도")
                try:
                    clean_message = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
                    retry_config = types.GenerateContentConfig(
                        system_instruction=self.persona.persona_text,
                        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                        temperature=0.8,
                    )
                    response = await self._call_gemini(clean_message, retry_config)
                    reply = self._extract_reply(response)
                    if reply:
                        logger.info(f"[클린 재시도] 응답: {reply}")
                        return reply, [], _meta
                except Exception as retry_e:
                    logger.warning(f"[클린 재시도] 실패: {retry_e}")
            _meta["error"] = f"client_error:{e.status}"
            return self.persona.error_message, [], _meta

        except Exception as e:
            self.chat_histories[user_id] = []
            logger.error(f"Gemini 에러: {type(e).__name__}: {e}")
            _meta["error"] = f"{type(e).__name__}"
            return self.persona.error_message, [], _meta

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

    @staticmethod
    def _clean_bot_content(text: str) -> str:
        """봇 자신의 메시지에서 유출된 메타데이터 정리 (맥락에 넣기 전)"""
        import re
        if not text:
            return text
        text = re.sub(r'feel\s*\([^)]*\)', '', text).strip()
        text = re.sub(r'/feel\s+[^\n]*', '', text).strip()
        text = re.sub(r'[\(\（]\s*feel\s*:[^)\）]*[\)\）]', '', text).strip()
        text = re.sub(r'\*\s*[\(\（]?감정\s*(변화|기록)[^*]*\*', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'\n---\s*\n.*', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'^function_call\s*:.*$', '', text, flags=re.MULTILINE | re.IGNORECASE).strip()
        text = re.sub(r'\[mood:[+-]?\d+\]', '', text).strip()
        text = re.sub(r'\*\*\*\*', '', text).strip()
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text).strip()
        return text[:150]

    def _format_msg_row(self, row: dict) -> str:
        """DB 메시지 행을 텍스트로 포맷."""
        if self.user and row["author_id"] == str(self.user.id):
            name = self.persona.name
            content = self._clean_bot_content(row["content"][:300])
        else:
            name = f"{row['author_name']}(<@{row['author_id']}>)"
            content = row["content"][:150]
        return f"{name}: {content}"

    async def _build_reply_chain(self, message) -> tuple[list[str], list[str], list, str | None]:
        """답글 체인 최근 5건. DB 우선, API 폴백. anchor(답글 대상 원본) message_id 반환."""
        if not message.reference or not message.reference.message_id:
            return [], [], [], None

        msg_id = str(message.id)
        channel_id = str(message.channel.id)

        # DB에서 reply_chain 조회
        db_chain = await self.librarian_db.get_reply_chain(msg_id, limit=5)
        if db_chain:
            chain = [self._format_msg_row(r) for r in db_chain]
            anchor_id = db_chain[0]["message_id"]  # 가장 오래된 메시지
            # 첨부파일은 DB에 없으므로 빈 리스트
            return chain, [], [], anchor_id

        # API 폴백
        logger.info("[맥락] reply_chain DB 미스 → API 폴백")
        chain = []
        seen_filenames = []
        chain_attachments = []
        raw_msgs = []
        current = message
        while current.reference and len(raw_msgs) < 5:
            ref = current.reference.resolved
            if not ref and current.reference.message_id:
                try:
                    ref = await current.channel.fetch_message(current.reference.message_id)
                except Exception:
                    break
            if not ref:
                break
            raw_msgs.append(ref)
            current = ref

        if raw_msgs:
            extras_list = await asyncio.gather(*(self._extract_extras(m) for m in raw_msgs))
        else:
            extras_list = []

        for ref, extras in zip(raw_msgs, extras_list):
            if self.user and ref.author.id == self.user.id:
                name = self.persona.name
                content = self._clean_bot_content(ref.content[:300])
            else:
                name = f"{ref.author.display_name}(<@{ref.author.id}>)"
                content = ref.content[:150]
            if extras:
                content = f"{content} {extras}" if content else extras
            for att in ref.attachments:
                seen_filenames.append(att.filename)
                chain_attachments.append(att)
            chain.append(f"{name}: {content}")

        anchor_id = str(raw_msgs[-1].id) if raw_msgs else None
        chain.reverse()
        return chain, seen_filenames, chain_attachments, anchor_id

    async def _build_context_messages(self, message, anchor_id: str | None = None) -> tuple[list[str], list[str], list[str]]:
        """맥락 메시지 수집. anchor 주변 5건 + 현재 직전 10건. DB 우선, API 폴백.
        반환: (anchor_context, recent_context, seen_ids)"""
        msg_id = str(message.id)
        channel_id = str(message.channel.id)
        seen_ids = set()

        # ── anchor 주변 5건 ──
        anchor_lines = []
        if anchor_id:
            before = await self.librarian_db.get_messages_before(channel_id, anchor_id, limit=2)
            after = await self.librarian_db.get_messages_after(channel_id, anchor_id, limit=2)
            # anchor 자체도 포함
            async with aiosqlite.connect(self.librarian_db.path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM message_log WHERE message_id = ?", (anchor_id,))
                anchor_row = await cursor.fetchone()

            if before or anchor_row or after:
                for r in before:
                    seen_ids.add(r["message_id"])
                    anchor_lines.append(self._format_msg_row(r))
                if anchor_row:
                    seen_ids.add(anchor_row["message_id"])
                    anchor_lines.append(self._format_msg_row(dict(anchor_row)))
                for r in after:
                    seen_ids.add(r["message_id"])
                    anchor_lines.append(self._format_msg_row(r))
            else:
                # DB 미스 → API 폴백
                logger.info("[맥락] anchor 주변 DB 미스 → API 폴백")
                try:
                    anchor_msg = await message.channel.fetch_message(int(anchor_id))
                    before_msgs = [m async for m in message.channel.history(limit=2, before=anchor_msg)]
                    after_msgs = [m async for m in message.channel.history(limit=2, after=anchor_msg)]
                    before_msgs.reverse()
                    for m in before_msgs + [anchor_msg] + after_msgs:
                        seen_ids.add(str(m.id))
                        if self.user and m.author.id == self.user.id:
                            name = self.persona.name
                            content = self._clean_bot_content(m.content[:300])
                        else:
                            name = f"{m.author.display_name}(<@{m.author.id}>)"
                            content = m.content[:150]
                        anchor_lines.append(f"{name}: {content}")
                except Exception as e:
                    logger.warning(f"[맥락] anchor API 폴백 실패: {e}")

        # ── 현재 직전 10건 ──
        recent_lines = []
        db_recent = await self.librarian_db.get_messages_recent(channel_id, msg_id, limit=10)
        if db_recent:
            for r in db_recent:
                if r["message_id"] not in seen_ids:
                    seen_ids.add(r["message_id"])
                    recent_lines.append(self._format_msg_row(r))
        else:
            # API 폴백
            logger.info("[맥락] 직전 대화 DB 미스 → API 폴백")
            try:
                msgs = [m async for m in message.channel.history(limit=10, before=message)]
                msgs.reverse()
                for m in msgs:
                    if str(m.id) not in seen_ids:
                        seen_ids.add(str(m.id))
                        if self.user and m.author.id == self.user.id:
                            name = self.persona.name
                            content = self._clean_bot_content(m.content[:300])
                        else:
                            name = f"{m.author.display_name}(<@{m.author.id}>)"
                            content = m.content[:150]
                        recent_lines.append(f"{name}: {content}")
            except Exception as e:
                logger.warning(f"[맥락] 직전 대화 API 폴백 실패: {e}")

        return anchor_lines, recent_lines

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

    def _trim_history(self, user_id: str):
        """히스토리를 MAX_HISTORY 턴으로 제한. function_call/response 쌍 보장."""
        history = self.chat_histories.get(user_id)
        if not history or len(history) <= MAX_HISTORY:
            return

        trimmed = history[-MAX_HISTORY:]

        # 검증: function_call/response 쌍이 깨진 턴 제거
        clean = []
        i = 0
        while i < len(trimmed):
            entry = trimmed[i]
            has_fc = any(hasattr(p, 'function_call') and p.function_call for p in entry.parts) if entry.parts else False
            has_fr = any(hasattr(p, 'function_response') and p.function_response for p in entry.parts) if entry.parts else False

            if has_fc and entry.role == "model":
                # function_call은 다음에 function_response가 와야 함
                if i + 1 < len(trimmed):
                    next_entry = trimmed[i + 1]
                    next_has_fr = any(hasattr(p, 'function_response') and p.function_response for p in next_entry.parts) if next_entry.parts else False
                    if next_has_fr:
                        clean.append(entry)
                        clean.append(next_entry)
                        i += 2
                        continue
                # 쌍 없으면 건너뜀
                i += 1
            elif has_fr and entry.role == "user":
                # 고아 function_response → 건너뜀
                i += 1
            else:
                clean.append(entry)
                i += 1

        self.chat_histories[user_id] = clean

    def _trim_perception_history(self, channel_id: str):
        """L1 채널별 히스토리를 MAX_PERCEPTION_HISTORY로 제한."""
        history = self.perception_histories.get(channel_id)
        if not history or len(history) <= MAX_PERCEPTION_HISTORY:
            return
        self.perception_histories[channel_id] = history[-MAX_PERCEPTION_HISTORY:]

    def _trim_evaluation_history(self):
        """L5 단일 히스토리를 MAX_EVALUATION_HISTORY로 제한."""
        if len(self.evaluation_history) <= MAX_EVALUATION_HISTORY:
            return
        self.evaluation_history[:] = self.evaluation_history[-MAX_EVALUATION_HISTORY:]

    async def _check_gift_message(self, message: discord.Message):
        """라이브러리 봇의 선물 메시지를 감지해서 5레이어 파이프라인으로 처리."""
        channel_id = str(message.channel.id)
        gift = await self.librarian_db.pop_pending_gift(channel_id)
        if not gift:
            return

        try:
            buyer_id = gift["buyer_id"]
            item_name = gift["item_name"]
            item_emoji = gift["item_emoji"]
            buyer_mention = f"<@{buyer_id}>"

            # 선물 맥락을 유저 메시지처럼 만들어서 5레이어 파이프라인에 태움
            gift_text = f"{buyer_mention} 님이 나에게 {item_emoji} {item_name}을(를) 선물해줬다"

            # 멘션 매핑
            mention_map = {}
            if message.guild:
                try:
                    member = message.guild.get_member(int(buyer_id))
                    if member:
                        mention_map[member.display_name] = buyer_id
                        buyer_name = member.display_name
                    else:
                        buyer_name = buyer_id
                except Exception:
                    buyer_name = buyer_id
            else:
                buyer_name = buyer_id

            # 5레이어 파이프라인 호출
            reply, files_to_send, _meta = await self._ask_gemini(
                user_id=buyer_id,
                user_name=buyer_name,
                user_text=gift_text,
                guild=message.guild,
                channel_id=channel_id,
            )

            if reply:
                await message.channel.send(reply)
                logger.info(f"[선물] 자체 발화: {reply[:100]}")

        except Exception as e:
            logger.warning(f"[선물] 처리 실패: {e}")



# ── v5: 레이어별 메서드 바인딩 ──
import importlib as _il

_perception = _il.import_module("librarian.layers.01_perception.perception")
AILibrarianBot._gather_context = _perception.gather_context
AILibrarianBot._run_perception = _perception.run_perception

_functioning = _il.import_module("librarian.layers.02_functioning.functioning")
AILibrarianBot._run_functioning = _functioning.run_functioning
AILibrarianBot._recognize_url_background = _functioning.recognize_url_background
AILibrarianBot._build_catalog = _functioning.build_catalog
AILibrarianBot._build_memories = _functioning.build_memories

_character = _il.import_module("librarian.layers.03_character.character")
AILibrarianBot._run_character = _character.run_character

_postprocess = _il.import_module("librarian.layers.04_postprocess.postprocess")
AILibrarianBot._run_postprocess = _postprocess.run_postprocess

_evaluation = _il.import_module("librarian.layers.05_evaluation.evaluation")
AILibrarianBot._run_evaluation = _evaluation.run_evaluation