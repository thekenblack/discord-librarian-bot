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
from config import (
    ADMIN_IDS, LIGHTNING_ADDRESS, GEMINI_MODEL, GEMINI_MODEL_L2, GEMINI_MODEL_L4,
    AI_MAX_OUTPUT_TOKENS, LOG_DIR,
    SPONTANEOUS_CHANNEL_ID, SPONTANEOUS_QUIET_HOURS, SPONTANEOUS_CHECK_HOURS, SPONTANEOUS_CHANCE,
    AI_HOURLY_WAGE,
)
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
MODEL_L2 = GEMINI_MODEL_L2
MODEL_L4 = GEMINI_MODEL_L4
from config import MAX_HISTORY_L1, MAX_HISTORY_L3, MAX_HISTORY_L5
MAX_HISTORY = MAX_HISTORY_L3
MAX_PERCEPTION_HISTORY = MAX_HISTORY_L1
MAX_EVALUATION_HISTORY = MAX_HISTORY_L5

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
    # variation selector (U+FE0F) 단독 매칭 방지 — 이모지 본체가 있을 때만 유효
    results = _UNICODE_EMOJI_RE.findall(raw)
    return [e for e in results if len(e.rstrip("\uFE0F")) > 0]


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
        self._mention_map: dict[str, str] = {}  # 닉네임 → user_id (L4 멘션 변환용)
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

        # 봇 지갑 생성 + 시급 태스크
        if self.user:
            from config import AI_NAME
            await self.library_db.get_or_create_wallet(str(self.user.id), AI_NAME)
            asyncio.create_task(self._hourly_wage_loop())

        # 자발적 발화
        if SPONTANEOUS_CHANNEL_ID:
            asyncio.create_task(self._spontaneous_loop())

        # 자발적 채널 대기 버퍼 (비멘션 메시지 debounce)
        self._spontaneous_pending: dict[str, asyncio.Task] = {}
        self._spontaneous_gen: dict[str, int] = {}

    async def _evaluation_worker(self):
        """L5 큐 워커. 큐에서 하나씩 꺼내서 순서대로 처리."""
        while True:
            got_item = False
            try:
                kwargs = await self._evaluation_queue.get()
                got_item = True
                await self._run_evaluation(**kwargs)
            except asyncio.CancelledError:
                if got_item:
                    self._evaluation_queue.task_done()
                return
            except Exception as e:
                logger.warning(f"[Evaluation Worker] 에러 (무시): {e}")
            finally:
                if got_item:
                    try:
                        self._evaluation_queue.task_done()
                    except ValueError:
                        pass

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
            # 다른 유저에게 답글이면 무시 (자발적 채널에서도)
            if message.reference and message.reference.resolved:
                ref = message.reference.resolved
                if self.user and ref.author.id != self.user.id:
                    return
            # 자발적 채널이면 debounce 후 응답 가능성
            if (SPONTANEOUS_CHANNEL_ID
                    and str(message.channel.id) == SPONTANEOUS_CHANNEL_ID):
                await self._handle_spontaneous_channel_message(message)
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
                    typing_channel=message.channel,
                )

        if not reply_text and not files_to_send:
            # 이모지 리액션
            if _meta.get("reaction"):
                for em in _extract_emojis(_meta["reaction"]):
                    try:
                        await message.add_reaction(em)
                        logger.info(f"이모지 리액션: {em}")
                    except discord.HTTPException as e:
                        logger.info(f"리액션 실패 (건너뜀): {em!r} → {e}")
                    except Exception as e:
                        logger.warning(f"리액션 실패: {em!r} → {e}")
                return
            if _meta.get("no_response"):
                logger.info("no_response → 메시지 안 보냄")
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
            # URL은 텍스트 그대로 전송 (디스코드 자동 프리뷰)
            await _send_reply(reply_text)

        # 이모지 리액션
        if _meta.get("reaction"):
            for em in _extract_emojis(_meta["reaction"]):
                try:
                    await message.add_reaction(em)
                except discord.HTTPException as e:
                    logger.info(f"리액션 실패 (건너뜀): {em!r} → {e}")
                except Exception as e:
                    logger.warning(f"리액션 실패: {em!r} → {e}")

    async def _ask_gemini(self, user_id: str,
                          user_name: str, user_text: str,
                          guild=None, reply_chain: list[str] = None,
                          anchor_context: list[str] = None,
                          recent_context: list[str] = None,
                          attachments: list = None,
                          seen_filenames: list[str] = None,
                          channel_id: str = None,
                          typing_channel=None,
                          is_spontaneous: bool = False,
                          preset_perception: str = None) -> tuple[str, list, dict]:
        """v5 5레이어: Perception → Functioning → Character → Postprocess → Evaluation"""
        import time as _time
        import re as _re
        _meta = {"tools_called": [], "tool_results": [], "error": None}
        _typing_task = None

        if user_id not in self.chat_histories:
            self.chat_histories[user_id] = []
        history = self.chat_histories[user_id]

        # L1 채널별 히스토리
        if channel_id and channel_id not in self.perception_histories:
            self.perception_histories[channel_id] = []

        try:
            # ── 공통 컨텍스트 (DB 1회 조회, 전 레이어 공유) ──
            _tc = _time.monotonic()
            bot_id = str(self.user.id) if self.user else ""
            shared_ctx = {
                "bot_emotion": await self.librarian_db.get_bot_emotion(),
                "user_emotion": await self.librarian_db.get_user_emotion(user_id),
                "user_summary": await self.librarian_db.get_user_summary(user_id),
                "channel_summary": await self.librarian_db.get_channel_summary(channel_id) if channel_id else "",
                "feedback": await self.librarian_db.get_feedback(user_id),
                "balance": await self.library_db.get_balance(bot_id) if bot_id else 0,
                "catalog": await self._build_catalog(),
                "memories": await self._build_memories(user_id, user_name),
            }
            logger.info(f"[공통 컨텍스트] 조립 완료 ({_time.monotonic()-_tc:.2f}s)")

            # ─�� Layer 1: Perception (맥락 파악) ──
            if preset_perception:
                perception = preset_perception
                logger.info(f"[L1 Perception] 스킵 (preset: {perception[:80]})")
            else:
                _t0 = _time.monotonic()
                raw_context = await self._gather_context(
                    user_id, user_name, guild, reply_chain,
                    anchor_context=anchor_context, recent_context=recent_context,
                    channel_id=channel_id, shared_ctx=shared_ctx)
                shared_ctx["raw_context"] = raw_context
                p_history = list(self.perception_histories.get(channel_id, [])) if channel_id else []
                perception = await self._run_perception(
                    user_id, user_name, user_text, raw_context,
                    history=p_history,
                    attachments=attachments, seen_filenames=seen_filenames,
                    is_spontaneous=is_spontaneous)
                # L1 히스토리에 이번 턴 추가 (asyncio 싱글 스레드라 락 불필요)
                if channel_id is not None:
                    self.perception_histories[channel_id].append(types.Content(role="user", parts=[
                        types.Part.from_text(text=f"{user_name}: {user_text}" if user_text else f"({user_name}이 빈 멘션을 보냈다.)")]))
                    self.perception_histories[channel_id].append(types.Content(role="model", parts=[
                        types.Part.from_text(text=perception)]))
                    self._trim_perception_history(channel_id)
                logger.info(f"[L1 Perception] 완료 ({_time.monotonic()-_t0:.2f}s)")

            # ── L1 응답 판정 (자발적 발화 전용) ──
            if is_spontaneous:
                if _re.search(r'decide_to_pause', perception or "", _re.IGNORECASE):
                    logger.info("[L1] 응답 판정: pause (추가 대기)")
                    _meta["wait"] = True
                    return "", [], _meta

                if _re.search(r'decide_to_ignore', perception or "", _re.IGNORECASE):
                    logger.info("[L1] 응답 판정: ignore (무시)")
                    _meta["ignore"] = True
                    return "", [], _meta

                if _re.search(r'decide_to_reply_to', perception or "", _re.IGNORECASE):
                    _meta["reply_to"] = True

                # 응답 판정 줄을 perception에서 제거 (L2/L3에 안 넘김)
                perception = _re.sub(r'\n?decide_to_\w+', '', perception, flags=_re.MULTILINE).strip()

            # ── typing 유지 (L2~전송 직전까지) ──
            if typing_channel:
                try:
                    _typing_task = asyncio.create_task(self._keep_typing(typing_channel))
                except Exception:
                    pass

            # ── Layer 2: Execution (도구 실행) ──
            _t0 = _time.monotonic()

            instruction, files_to_send, processor_meta = await self._run_functioning(
                user_id=user_id, user_name=user_name, user_text=user_text,
                attachments=attachments, seen_filenames=seen_filenames,
                perception=perception, channel_id=channel_id,
                shared_ctx=shared_ctx,
            )
            _meta["tools_called"] = processor_meta.get("tools_called", [])
            _meta["tool_results"] = processor_meta.get("tool_results", [])
            if processor_meta.get("shared_urls"):
                _meta["shared_urls"] = processor_meta["shared_urls"]
            if processor_meta.get("reaction"):
                _meta["reaction"] = processor_meta["reaction"]
            if processor_meta.get("gifts"):
                _meta["gifts"] = processor_meta["gifts"]
            logger.info(f"[L2 Execution] 완료 ({_time.monotonic()-_t0:.2f}s)")

            # ── 선물 즉시 처리 (L3 이전에 알림) ──
            if _meta.get("gifts") and typing_channel:
                from config import AI_NAME
                from library.cogs.shop import SHOP_MAP
                from library.utils import sat_fmt
                bot_id = str(self.user.id) if self.user else ""
                for gift in _meta["gifts"]:
                    try:
                        item_data = SHOP_MAP.get(gift.get("item_id"))
                        item_price = item_data["price"] if item_data else 0
                        new_bal = await self.library_db.spend_balance(
                            bot_id, item_price, note=f"{gift['item_emoji']} {gift['item_name']}",
                            item_emoji=gift["item_emoji"], item_name=gift["item_name"],
                            item_price=item_price)
                        if new_bal is None:
                            logger.info(f"[선물] 잔고 부족으로 선물 실패: {gift['item_name']}")
                            continue
                        if gift.get("item_id", "").startswith("tip_"):
                            await self.library_db.charge_balance(user_id, user_name, item_price)
                        await self.librarian_db.save_gift_log(
                            buyer_id=bot_id, buyer_name=AI_NAME,
                            item_emoji=gift["item_emoji"], item_name=gift["item_name"],
                            item_price=item_price, message=gift.get("message"),
                            recipient_id=user_id, recipient_name=user_name)
                        # 라이브러리 봇으로 알림
                        lib_client = getattr(self, "library_bot_client", None)
                        if lib_client:
                            lib_channel = lib_client.get_channel(typing_channel.id)
                            if lib_channel:
                                gift_desc = (
                                    f"{gift['item_emoji']} **{AI_NAME}**이(가) "
                                    f"**{user_name}** 님에게 "
                                    f"**{gift['item_name']}**을(를) 선물했습니다! ({sat_fmt(item_price)})"
                                )
                                gift_msg = gift.get("message", "")
                                if gift_msg:
                                    gift_desc += f"\n> {AI_NAME}: \"{gift_msg}\""
                                embed = discord.Embed(description=gift_desc, color=0xF1C40F)
                                embed.set_footer(text="/charge 로 충전 · /buy 로 선물")
                                await lib_channel.send(embed=embed)
                    except Exception as e:
                        logger.warning(f"[선물] 처리 실패: {e}")

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
                _meta["no_response"] = True
                return "", files_to_send, _meta

            # ── Layer 4: Postprocess (디스코드 포매팅) ──
            _t0 = _time.monotonic()
            # 현재 메시지 발화자도 mention_map에 추가
            self._mention_map[user_name] = user_id
            reply = await self._run_postprocess(
                raw_reply, user_name,
                mention_map=dict(self._mention_map),
                instruction=instruction)
            if reply != raw_reply:
                logger.info(f"[L4 Postprocess] 정제 ({_time.monotonic()-_t0:.2f}s)")
            else:
                logger.info(f"[L4 Postprocess] 통과 ({_time.monotonic()-_t0:.2f}s)")

            # 히스토리에 L4 변환 전 원본 저장 (L3가 깨끗한 히스토리를 보도록)
            history.append(types.Content(role="model", parts=[types.Part.from_text(text=raw_reply)]))
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

        finally:
            if _typing_task and not _typing_task.done():
                _typing_task.cancel()

    @staticmethod
    async def _keep_typing(channel):
        """typing 상태를 유지. cancel되면 종료."""
        try:
            while True:
                await channel.typing()
                await asyncio.sleep(8)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _call_gemini(self, contents, config, max_retries=3, retry_delay=1.0, model=None):
        """API 호출 (비동기). 실패 시 재시도."""
        _model = model or MODEL
        last_err = None
        loop = asyncio.get_event_loop()
        for attempt in range(max_retries):
            try:
                return await loop.run_in_executor(
                    None,
                    lambda: self._gemini_client.models.generate_content(
                        model=_model, contents=contents, config=config,
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
        """DB 메시지 행을 텍스트로 포맷. @닉네임 형태 통일."""
        if self.user and row["author_id"] == str(self.user.id):
            name = self.persona.name
            content = self._clean_bot_content(row["content"][:300])
        else:
            name = f"@{row['author_name']}"
            self._mention_map[row["author_name"]] = row["author_id"]
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
                name = f"@{ref.author.display_name}"
                self._mention_map[ref.author.display_name] = str(ref.author.id)
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
                            name = f"@{m.author.display_name}"
                            self._mention_map[m.author.display_name] = str(m.author.id)
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
                            name = f"@{m.author.display_name}"
                            self._mention_map[m.author.display_name] = str(m.author.id)
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
            # 가격 정보: SHOP_MAP에서 조회
            from library.cogs.shop import SHOP_MAP, SHOP_PAGE1, SHOP_PAGE2
            item_data = SHOP_MAP.get(gift.get("item_id"))
            if item_data:
                price = item_data["price"]
                is_page2 = any(i["id"] == item_data["id"] for i in SHOP_PAGE2)
                if is_page2:
                    cheaper = sum(1 for i in SHOP_PAGE2 if i["price"] < price)
                    rank = int(cheaper / len(SHOP_PAGE2) * 100)
                    price_info = f" ({price} sat, 이상한 아이템 중 상위 {100 - rank}%)"
                else:
                    cheaper = sum(1 for i in SHOP_PAGE1 if i["price"] < price)
                    rank = int(cheaper / len(SHOP_PAGE1) * 100)
                    price_info = f" ({price} sat, 일반 아이템 중 상위 {100 - rank}%)"
            else:
                price_info = ""
            gift_text = f"{buyer_mention} 님이 나에게 {item_emoji} {item_name}을(를) 선물해줬다{price_info}"

            # fullness/hydration만 직접 적용 (mood/energy는 L5가 판단)
            effects_str = gift.get("effects", "")
            fh_effects = {}
            for pair in effects_str.split(","):
                if ":" not in pair:
                    continue
                k, v = pair.split(":", 1)
                k = k.strip()
                if k in ("fullness", "hydration"):
                    try:
                        fh_effects[k] = float(v)
                    except ValueError:
                        pass
            if fh_effects:
                try:
                    await self.librarian_db.update_emotion(fh_effects)
                except Exception:
                    pass

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

    # ── 시급 지급 ─────────────────────────────────────────

    async def _hourly_wage_loop(self):
        """매시간 시급 지급 + fullness/hydration 감소 + 자동 소비."""
        import random
        await self.wait_until_ready()
        await asyncio.sleep(60)
        while True:
            await asyncio.sleep(3600)
            try:
                if not self.user:
                    continue
                bot_id = str(self.user.id)
                from config import AI_NAME
                from library.cogs.shop import SHOP_PAGE1

                # 시급 지급
                new_bal = await self.library_db.charge_balance(bot_id, AI_NAME, AI_HOURLY_WAGE)
                logger.info(f"[시급] +{AI_HOURLY_WAGE} sat (잔고: {new_bal})")

                # fullness/hydration 감소 (높을수록 빠르게)
                bot_emo = await self.librarian_db.get_bot_emotion()
                for axis in ("fullness", "hydration"):
                    current = bot_emo.get(axis, 50)
                    decay = max(1, current * 0.06)
                    await self.librarian_db.update_emotion({axis: -decay})
                balance = await self.library_db.get_balance(bot_id)

                # 자동 식사: 확률 = (100 - fullness)%
                fullness = bot_emo.get("fullness", 50)
                if random.randint(1, 100) > fullness:
                    food_items = [i for i in SHOP_PAGE1 if i["effects"].get("fullness") and i["price"] <= balance]
                    if food_items:
                        item = self._pick_by_wealth(food_items, balance)
                        result = await self.library_db.spend_balance(
                            bot_id, item["price"], note=f"{item['emoji']} {item['name']}",
                            item_emoji=item["emoji"], item_name=item["name"], item_price=item["price"])
                        if result is not None:
                            effects = {ek: ev for ek in ("fullness", "hydration", "self_mood", "self_energy")
                                       if (ev := item["effects"].get(ek))}
                            if effects:
                                await self.librarian_db.update_emotion(effects)
                            await self.librarian_db.save_gift_log(
                                buyer_id=bot_id, buyer_name=AI_NAME,
                                item_emoji=item["emoji"], item_name=item["name"],
                                item_price=item["price"])
                            logger.info(f"[자동소비] {item['emoji']} {item['name']} ({item['price']} sat)")

                # 자동 음료: 확률 = (100 - hydration)%
                # 식사 후 갱신된 수분/잔고 반영 (라면 등 수분 보충 효과)
                bot_emo = await self.librarian_db.get_bot_emotion()
                hydration = bot_emo.get("hydration", 50)
                balance = await self.library_db.get_balance(bot_id)
                if random.randint(1, 100) > hydration:
                    drink_items = [i for i in SHOP_PAGE1 if i["effects"].get("hydration") and not i["effects"].get("fullness") and i["price"] <= balance]
                    if drink_items:
                        item = self._pick_by_wealth(drink_items, balance)
                        result = await self.library_db.spend_balance(
                            bot_id, item["price"], note=f"{item['emoji']} {item['name']}",
                            item_emoji=item["emoji"], item_name=item["name"], item_price=item["price"])
                        if result is not None:
                            effects = {ek: ev for ek in ("fullness", "hydration", "self_mood", "self_energy")
                                       if (ev := item["effects"].get(ek))}
                            if effects:
                                await self.librarian_db.update_emotion(effects)
                            await self.librarian_db.save_gift_log(
                                buyer_id=bot_id, buyer_name=AI_NAME,
                                item_emoji=item["emoji"], item_name=item["name"],
                                item_price=item["price"])
                            logger.info(f"[자동소비] {item['emoji']} {item['name']} ({item['price']} sat)")

            except Exception as e:
                logger.warning(f"[시급] 오류: {e}")

    @staticmethod
    def _pick_by_wealth(items: list[dict], balance: int) -> dict:
        """잔고 수준에 따른 가중치 선택. 부자일수록 비싼 거 확률 올라감."""
        import random
        weights = [min(i["price"], balance / 3) for i in items]
        return random.choices(items, weights=weights, k=1)[0]

    # ── 자발적 발화 ─────────────────────────────────────

    async def _spontaneous_loop(self):
        """마지막 메시지 이후 일정 ��간 침묵 → 주기적 확률 체크 → 발화."""
        import random
        from datetime import datetime, timezone
        await self.wait_until_ready()
        await asyncio.sleep(60)

        check_interval = SPONTANEOUS_CHECK_HOURS * 3600
        quiet_threshold = SPONTANEOUS_QUIET_HOURS * 3600

        while True:
            await asyncio.sleep(check_interval)
            try:
                channel = self.get_channel(int(SPONTANEOUS_CHANNEL_ID))
                if not channel:
                    continue

                # 마지막 메시지 시각 확인
                last_msg = None
                async for msg in channel.history(limit=1):
                    last_msg = msg
                if not last_msg:
                    continue

                elapsed = (datetime.now(timezone.utc) - last_msg.created_at).total_seconds()
                if elapsed < quiet_threshold:
                    continue

                # 확률 체크
                if random.randint(1, 100) > SPONTANEOUS_CHANCE:
                    logger.info(f"[자발적 발화] 확률 미달 (침묵 {elapsed/3600:.1f}h)")
                    continue

                await self._spontaneous_speak(channel)
            except Exception as e:
                logger.warning(f"[자발적 발화] 오류: {e}")

    async def _spontaneous_speak(self, channel):
        """자발적 채널에서 발화. 평소 파이프라인 그대로, 프롬프트만 다름."""
        channel_id = str(channel.id)

        spontaneous_text = (
            "(한동안 아무도 없다. 도서관이 조용하다. 심심하다.)\n"
            "(책장을 둘러보거나, 전에 누가 했던 얘기를 떠올리거나, "
            "요즘 세상이 어떻게 돌아가는지 궁금해지거나. "
            "뭐라도 하고 싶은 기분이다.)\n"
            "(혼잣말을 해도 되고, 아무 말도 안 해도 된다. no_comment 가능.)"
        )
        reply, files_to_send, _meta = await self._ask_gemini(
            user_id="spontaneous",
            user_name="system",
            user_text=spontaneous_text,
            guild=channel.guild if hasattr(channel, 'guild') else None,
            channel_id=channel_id,
        )

        if _meta.get("no_response"):
            logger.info("[자발적 발화] no_response")
            return

        if reply:
            await channel.send(reply)
            logger.info(f"[자발적 발화] {reply[:100]}")

    # ── 자발적 채널 비멘션 응답 (debounce) ────────────────

    async def _handle_spontaneous_channel_message(self, message: discord.Message):
        """자발적 채널에서 비멘션 메시지 처리. debounce 후 응답 여부 결정."""
        debounce_key = f"{message.channel.id}:{message.author.id}"

        # generation 카운터로 최신 메시지만 처리
        gen = self._spontaneous_gen.get(debounce_key, 0) + 1
        self._spontaneous_gen[debounce_key] = gen

        # 기존 대기 취소 (best effort)
        old_task = self._spontaneous_pending.get(debounce_key)
        if old_task and not old_task.done():
            old_task.cancel()

        self._spontaneous_pending[debounce_key] = asyncio.create_task(
            self._debounced_spontaneous_reply(message, gen, debounce_key))

    async def _debounced_spontaneous_reply(self, message: discord.Message, gen: int, debounce_key: str):
        """debounce 대기 후 비멘션 응답. gen이 최신이 아니면 중단."""
        channel_id = str(message.channel.id)
        await asyncio.sleep(3)  # 3초 대기 (debounce)

        # sleep 후 자기가 최신인지 확인
        if self._spontaneous_gen.get(debounce_key) != gen:
            return

        try:
            text = message.content
            if message.mentions:
                for user in message.mentions:
                    text = text.replace(f"<@{user.id}>", f"@{user.display_name}")
                    text = text.replace(f"<@!{user.id}>", f"@{user.display_name}")

            uid = str(message.author.id)
            if uid not in self._user_locks:
                self._user_locks[uid] = asyncio.Lock()
            async with self._user_locks[uid]:
                attachments = list(message.attachments) if message.attachments else []
                seen_filenames = [a.filename for a in attachments] if attachments else []
                extras = await self._extract_extras(message)
                if extras:
                    text = f"{text} {extras}"

                reply, files_to_send, _meta = await self._ask_gemini(
                    user_id=uid,
                    user_name=message.author.display_name,
                    user_text=text,
                    guild=message.guild,
                    attachments=attachments,
                    seen_filenames=seen_filenames,
                    channel_id=channel_id,
                    typing_channel=message.channel,
                    is_spontaneous=True,
                )

            if _meta.get("no_response") or _meta.get("ignore"):
                return

            if _meta.get("wait"):
                # 상대가 아직 말하는 중이라고 판단 → 추가 대기
                await asyncio.sleep(5)
                # 새 메시지가 왔으면 중단 (다음 debounce가 처리)
                if self._spontaneous_gen.get(debounce_key) != gen:
                    return
                # 새 메시지 없음 → 말을 하다 멈춤, L1 스킵하고 L2부터 진행
                logger.info("[자발적 채널] wait 후 추가 대기 만료 → 말을 하다 멈춤")
                async with self._user_locks[uid]:
                    if self._spontaneous_gen.get(debounce_key) != gen:
                        return
                    reply, files_to_send, _meta = await self._ask_gemini(
                        user_id=uid,
                        user_name=message.author.display_name,
                        user_text=text,
                        guild=message.guild,
                        attachments=attachments,
                        seen_filenames=seen_filenames,
                        channel_id=channel_id,
                        typing_channel=message.channel,
                        preset_perception=f"{message.author.display_name}이(가) 메시지를 끊어서 보내다가 멈췄다. 말이 끝난 건지 하다 만 건지 모호하다.",
                    )
                if _meta.get("no_response"):
                    return

            if reply:
                if _meta.get("reply_to"):
                    await message.reply(reply, files=files_to_send or None)
                else:
                    if files_to_send:
                        await message.channel.send(reply, files=files_to_send)
                    else:
                        await message.channel.send(reply)
                logger.info(f"[자발적 채널] {message.author.display_name}: {text[:50]} → {reply[:100]}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"[자발적 채널] 응답 오류: {e}")


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