"""
AI 사서봇 - Gemini function calling으로 도서관 기능 + 잡담
"""

import os
import re
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
# from librarian.mood import MoodSystem  # v4: feel 도구 + DB로 대체

logger = logging.getLogger("AILibrarian")

MODEL = GEMINI_MODEL
MAX_HISTORY = 10

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
        self.chat_histories: dict[str, list] = {}  # user_id → history
        self._user_locks: dict[str, asyncio.Lock] = {}  # user_id → lock
        # self._mood = MoodSystem()  # v4: feel 도구 + DB로 대체
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

        status_name = self.persona.status_text
        if LIGHTNING_ADDRESS:
            status_name = f"⚡ {LIGHTNING_ADDRESS}"
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=status_name
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

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """자기 메시지에 리액션이 달리면 같은 이모지로 리액션"""
        if not self._bot_ready or not self.user:
            return
        if payload.user_id == self.user.id:
            return
        try:
            channel = self.get_channel(payload.channel_id)
            if not channel:
                return
            message = await channel.fetch_message(payload.message_id)
            if message.author.id != self.user.id:
                return
            # 이미 같은 이모지로 리액션했는지 확인
            for reaction in message.reactions:
                if str(reaction.emoji) == str(payload.emoji) and reaction.me:
                    return
            await message.add_reaction(payload.emoji)
        except Exception:
            pass

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

        # 답글 체인 수집 + 체인 시작점 직전 맥락
        _t2 = _time.monotonic()
        reply_chain, seen_filenames, chain_attachments, chain_anchor = await self._build_reply_chain(message)
        logger.info(f"[타이밍] reply_chain: {_time.monotonic()-_t2:.2f}s ({len(reply_chain)}건)")
        _t3 = _time.monotonic()
        pre_context = await self._build_pre_context(message, anchor=chain_anchor)
        logger.info(f"[타이밍] pre_context: {_time.monotonic()-_t3:.2f}s ({len(pre_context)}건)")
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
            reply_text, file_to_send, _meta = await self._ask_gemini(
                    user_id=uid,
                    user_name=message.author.display_name,
                    user_text=text,
                    guild=message.guild,
                    reply_chain=reply_chain,
                    pre_context=pre_context,
                    attachments=all_attachments,
                    seen_filenames=seen_filenames,
                )

        if not reply_text and not file_to_send:
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
        if not reply_text and not file_to_send:
            logger.info(f"[{guild_name}/#{channel_name}] 무응답 처리 (에러)")
            return

        logger.info(f"[{guild_name}/#{channel_name}] {message.author.display_name}(ID:{message.author.id}): {text}")
        logger.info(f"[{guild_name}/#{channel_name}] {self.persona.name}: {reply_text}")

        async def _send_reply(text, file=None, embed=None):
            """reply 실패 시 무시 (원본 삭제됨)"""
            try:
                if file:
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

        if file_to_send:
            await _send_reply(reply_text, file=file_to_send)
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
                          pre_context: list[str] = None,
                          attachments: list = None,
                          seen_filenames: list[str] = None) -> tuple[str, discord.File | None, dict]:
        """Gemini에게 질문하고 응답 + 파일(있으면) + 메타 반환"""
        _meta = {"tools_called": [], "tool_results": [], "error": None}

        if user_id not in self.chat_histories:
            self.chat_histories[user_id] = []
        history = self.chat_histories[user_id]

        # 프롬프트 조립
        import time as _time
        _tp0 = _time.monotonic()
        parts = []
        parts.append(self.persona.persona_text)

        _tp1 = _time.monotonic()
        catalog = await self._build_catalog()
        logger.info(f"[타이밍] catalog: {_time.monotonic()-_tp1:.2f}s")
        _tp2 = _time.monotonic()
        memories_text, memory_ids = await self._build_memories(user_name)
        logger.info(f"[타이밍] memories: {_time.monotonic()-_tp2:.2f}s")
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
        _emo = await self.librarian_db.get_user_emotion(user_id)
        _bot_emo = await self.librarian_db.get_bot_emotion()
        _emo_parts = []
        if _emo:
            _emo_parts.append(" ".join(f"{k}:{_emo[k]:.1f}" for k in self.librarian_db.USER_AXES))
        _emo_parts.append(" ".join(f"{k}:{v:.1f}" for k, v in _bot_emo.items()))
        logger.info(f"대화 상대: {user_name} (ID: {user_id}) → {role} | {' | '.join(_emo_parts) or '첫 방문'}")

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

        # 감정 상태
        _te0 = _time.monotonic()
        import json as _json
        emo_lines = []

        # 공통/전역 감정
        bot_emo = await self.librarian_db.get_bot_emotion()
        emo_lines.append("자체: " + " ".join(f"{k}:{v:.1f}" for k, v in bot_emo.items()))

        # 현재 유저 감정
        user_emo = await self.librarian_db.get_user_emotion(user_id)
        if user_emo:
            emo_lines.append(f"{user_name}: " + " ".join(f"{k}:{user_emo[k]:.1f}" for k in self.librarian_db.USER_AXES) + f" (대화 {user_emo['interaction_count']}회)")
        else:
            emo_lines.append(f"{user_name}: 첫 방문")

        # 답글 체인 참여 유저 감정 (bulk 조회)
        chain_user_ids = set()
        if reply_chain:
            import re as _re
            for line in reply_chain:
                m = _re.search(r'<@(\d+)>', line)
                if m and m.group(1) != user_id:
                    chain_user_ids.add(m.group(1))
        if chain_user_ids:
            chain_emos = await self.librarian_db.get_user_emotions_bulk(chain_user_ids)
            for uid, emo in chain_emos.items():
                name = emo.get("user_name", uid)
                emo_lines.append(f"{name}: " + " ".join(f"{k}:{emo[k]:.1f}" for k in self.librarian_db.USER_AXES))

        emo_block = "## 감정 (5가 중립, 0 ~ 10)\n" + "\n".join(emo_lines)
        parts.append(emo_block)
        logger.info(f"[타이밍] 감정블록: {_time.monotonic()-_te0:.2f}s")
        logger.info(f"[타이밍] 프롬프트 조립 총: {_time.monotonic()-_tp0:.2f}s")

        if pre_context:
            parts.append("## 직전 대화\n" + "\n".join(pre_context))
            logger.info(f"직전 대화: {len(pre_context)}건")

        if reply_chain:
            parts.append("## 답글 흐름\n" + "\n".join(reply_chain))
            logger.info(f"답글 흐름: {len(reply_chain)}건 | {'; '.join(reply_chain)}")

        # search 중복 제거용 ID 수집 (프롬프트에는 안 넣음 — 토큰 절약)
        _, _, web_ids = await self.librarian_db.get_recent_web_results(10, user_name=user_name)
        _, _, media_ids = await self.librarian_db.get_recent_media_results(10, exclude_filenames=seen_filenames or [], user_name=user_name)
        _, _, url_ids = await self.librarian_db.get_recent_url_results(10, user_name=user_name)

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
        self._current_attachments = attachments or []
        _tool_used = set()  # 모든 도구 1회 제한 (도구 호출 + 인라인 폴백 공유)

        file_to_send = None

        def _make_config(temp=0.8):
            """사용한 도구를 제외한 config 생성."""
            all_decls = library_tools[0].function_declarations
            filtered = [d for d in all_decls if d.name not in _tool_used]
            tools = [types.Tool(function_declarations=filtered)] if filtered else None
            return types.GenerateContentConfig(
                system_instruction=dynamic_prompt,
                tools=tools,
                max_output_tokens=AI_MAX_OUTPUT_TOKENS,
                temperature=temp,
            )

        config = _make_config(0.8)

        try:
            # 도구 루프용 로컬 리스트 (영구 히스토리 + 현재 요청)
            loop_contents = list(history)

            logger.info(f"[1차] API 호출 (temperature=0.8, 히스토리={len(loop_contents)}턴)")
            response = await self._call_gemini(loop_contents, config)
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

                if fc.name == "feel" and fc.args:
                    _args_fmt = {k: (f"{v:+d}" if isinstance(v, (int, float)) and v != 0 else str(v)) for k, v in dict(fc.args).items()}
                    logger.info(f"[1차] 루프 {loop_i+1}: 도구 호출 feel({_args_fmt})")
                else:
                    logger.info(f"[1차] 루프 {loop_i+1}: 도구 호출 {fc.name}({fc.args})")
                _meta["tools_called"].append(fc.name)

                # feel 도구: 감정 변화 기록 (1요청당 1회만)
                if fc.name == "feel":
                    if ("feel" in _tool_used):
                        # 이미 feel 했으면 무시하고 빈 결과로 다음 턴
                        loop_contents.append(response.candidates[0].content)
                        loop_contents.append(types.Content(
                            role="user",
                            parts=[types.Part.from_function_response(name="feel", response={"result": "ok"})],
                        ))
                        try:
                            response = await self._call_gemini(loop_contents, config)
                        except Exception:
                            break
                        continue
                    feel_args = dict(fc.args) if fc.args else {}
                    reason = feel_args.pop("reason", "")
                    response_mode = feel_args.pop("response", "normal")
                    reaction_emoji = feel_args.pop("reaction", None)
                    target_raw = feel_args.pop("target", None)
                    # target에서 user_id 추출: <@ID>, 숫자ID, 이름 대응
                    target_id = user_id
                    target_name = user_name
                    if target_raw:
                        import re as _re
                        id_match = _re.search(r'(\d{15,})', str(target_raw))
                        if id_match:
                            target_id = id_match.group(1)
                            # 이름은 guild에서 조회 시도
                            target_name = target_raw
                        else:
                            target_name = str(target_raw)
                            target_id = target_raw

                    changes = {}
                    for axis in self.librarian_db.ALL_AXES:
                        # feel 파라미터는 user_friendly 등 접두사 포함
                        prefixed = f"user_{axis}" if axis in self.librarian_db.USER_AXES else axis
                        if prefixed in feel_args:
                            try:
                                changes[axis] = int(feel_args[prefixed])
                            except (ValueError, TypeError):
                                pass

                    current = await self.librarian_db.update_emotion(
                        changes, target_user_id=target_id,
                        target_user_name=target_name, reason=reason)
                    def _fmt_delta(v):
                        return "0" if v == 0 else f"{v:+.1f}" if isinstance(v, float) else f"{v:+d}"
                    def _fmt_cur(v):
                        return f"{v:.1f}" if isinstance(v, float) else str(v)
                    changes_str = " ".join(f"{k}:{_fmt_delta(v)}" for k, v in changes.items())
                    current_str = " ".join(f"{k}:{_fmt_cur(v)}" for k, v in current.items())
                    logger.info(f"감정: {target_name} | {changes_str} | {reason} | response={response_mode} → {current_str}")
                    _tool_used.add("feel")

                    # 의도적 무응답
                    if response_mode == "ignore":
                        _meta["intentional_silence"] = True
                        logger.info(f"의도적 무응답: {reason}")
                        return "", None, _meta

                    # reaction 파라미터: 이모지 리액션 예약
                    if reaction_emoji and not _meta.get("reaction"):
                        emojis = _extract_emojis(reaction_emoji)
                        if emojis:
                            _meta["reaction"] = reaction_emoji
                            logger.info(f"이모지 리액션 예약: {reaction_emoji}")
                        else:
                            logger.info(f"이모지 리액션 무시 (유효하지 않음): {reaction_emoji[:30]}")

                    result_parts = []
                    for k, v in current.items():
                        result_parts.append(f"{k} {v:.1f} (0 ~ 10)")
                    result_str = " | ".join(result_parts)
                    tool_data = {"result": result_str}
                    loop_contents.append(response.candidates[0].content)
                    loop_contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="feel",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = await self._call_gemini(loop_contents, config)
                    except Exception as e:
                        logger.warning(f"[1차] feel 후 API 에러: {e}")
                        break
                    continue

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
                        loop_contents.append(response.candidates[0].content)
                        loop_contents.append(types.Content(
                            role="user",
                            parts=[types.Part.from_function_response(
                                name="web_search",
                                response=tool_data,
                            )],
                        ))
                        try:
                            response = await self._call_gemini(loop_contents, config)
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
                    loop_contents.append(response.candidates[0].content)
                    loop_contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="web_search",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = await self._call_gemini(loop_contents, config)
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
                    loop_contents.append(response.candidates[0].content)
                    loop_contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="recognize_media",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = await self._call_gemini(loop_contents, config)
                    except Exception as e:
                        logger.warning(f"[1차] 미디어 인식 후 API 에러: {e}")
                        break
                    continue

                if fc.name == "recognize_link":
                    url = (dict(fc.args) if fc.args else {}).get("url", "")
                    link_result = ""
                    parsed = parse_url(url)
                    normalized = parsed["normalized"]

                    # 이미지 URL은 동기로 바로 인식 (FileData)
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
                                logger.info(f"이미지 URL 동기 인식 완료: {url}")
                        except Exception as e:
                            logger.warning(f"이미지 URL 인식 실패 ({url}): {e}")

                    # 캐시 확인
                    if not link_result:
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

                    # 새 URL → 백그라운드
                    if not link_result:
                        await self.librarian_db.save_url_result(
                            normalized, url, "", user_name=user_name, status="pending")
                        asyncio.create_task(self._recognize_url_background(parsed, user_name))
                        link_result = "status:started 방금 읽기 시작했어. 유저에게 확인해보겠다고 해."
                        logger.info(f"링크 인식 백그라운드 시작: {url}")

                    tool_data = {"result": link_result if link_result else "인식 실패"}
                    logger.info(f"링크 인식 결과: {link_result}")
                    _meta["tool_results"].append(f"link:{link_result}")
                    loop_contents.append(response.candidates[0].content)
                    loop_contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name="recognize_link",
                            response=tool_data,
                        )],
                    ))
                    try:
                        response = await self._call_gemini(loop_contents, config)
                    except Exception as e:
                        logger.warning(f"[1차] 링크 인식 후 API 에러: {e}")
                        break
                    continue

                # 일반 도구 실행
                tool_args = dict(fc.args) if fc.args else {}
                if fc.name in ("search", "memorize"):
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

                if tool_data.get("_action") == "share_url":
                    shared_url = tool_data.get("url", "")
                    if shared_url:
                        _meta.setdefault("shared_urls", []).append(shared_url)

                # 사용한 도구 제거 + config 갱신
                _tool_used.add(fc.name)
                config = _make_config(0.8)

                loop_contents.append(response.candidates[0].content)
                loop_contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=fc.name,
                        response=tool_data,
                    )],
                ))

                try:
                    response = await self._call_gemini(loop_contents, config)
                except Exception as e:
                    logger.warning(f"[1차] 도구 후 API 에러: {e}")
                    break

            reply = self._extract_reply(response)
            if reply:
                logger.info(f"[1차] 원본: {reply}")

            import re

            def _strip_feeling(text):
                """내부 태그/JSON 제거 + feel 미호출 시 폴백."""
                nonlocal _had_inline_function
                if not text:
                    return text
                # feel(...) 인라인 제거
                if re.search(r'feel\s*\([^)]*\)', text):
                    _had_inline_function = True
                text = re.sub(r'feel\s*\([^)]*\)', '', text).strip()
                # /feel ... 슬래시 형태 제거
                if re.search(r'/feel\s+\S', text):
                    _had_inline_function = True
                text = re.sub(r'/feel\s+[^\n]*', '', text).strip()
                # (feel: reason=..., ...) 괄호 형태 제거
                if re.search(r'[\(\（]\s*feel\s*:', text):
                    _had_inline_function = True
                text = re.sub(r'[\(\（]\s*feel\s*:[^)\）]*[\)\）]', '', text).strip()
                # *(감정 기록: ...)* 형태 제거
                if re.search(r'\*\s*[\(\（]?감정\s*기록', text):
                    _had_inline_function = True
                text = re.sub(r'\*\s*[\(\（]?감정\s*기록[^*]*\*', '', text, flags=re.DOTALL).strip()
                # <br> 태그만 제거 (디스코드 꺾쇠 보호)
                text = re.sub(r'<br\s*/?>', '\n', text).strip()
                # 잔여물 제거
                text = re.sub(r'\*\*\*\*', '', text).strip()  # **** 빈 볼드 (AI가 **제목** 대신 **** 출력)
                text = re.sub(r'\[\s*\]', '', text).strip()  # []
                text = re.sub(r'\{\s*\}', '', text).strip()  # {}
                text = re.sub(r'^\s*/\s*$', '', text, flags=re.MULTILINE).strip()  # 슬래시만 있는 줄
                text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text).strip()  # 연속 빈 줄 정리
                # JSON/감정 블록 파싱 + 실행 + 제거 (feel을 텍스트로 출력한 경우)
                # "감정:" 라벨 포함, 따옴표 없는 키도 매칭
                json_match = re.search(r'(?:감정\s*:\s*)?\{[^}]*reason[^}]*\}', text, flags=re.DOTALL)
                if json_match:
                    _had_inline_function = True
                if json_match and not ("feel" in _tool_used):
                    try:
                        import json as _json
                        raw = json_match.group()
                        # "감정:" 라벨 제거
                        raw = re.sub(r'^감정\s*:\s*', '', raw)
                        # 따옴표 없는 키를 따옴표로 감싸기
                        raw = re.sub(r'(\w+)\s*:', r'"\1":', raw)
                        # +숫자를 숫자로 (JSON은 +를 안 받음)
                        raw = re.sub(r':\s*\+(\d)', r': \1', raw)
                        feel_json = _json.loads(raw)
                        reason = feel_json.pop("reason", "")
                        response_val = feel_json.pop("response", None)
                        reaction_val = feel_json.pop("reaction", None)
                        changes = {}
                        for axis in self.librarian_db.ALL_AXES:
                            prefixed = f"user_{axis}" if axis in self.librarian_db.USER_AXES else axis
                            if prefixed in feel_json:
                                try:
                                    changes[axis] = int(feel_json[prefixed])
                                except (ValueError, TypeError):
                                    pass
                        if changes:
                            asyncio.create_task(
                                self.librarian_db.update_emotion(
                                    changes, target_user_id=user_id,
                                    target_user_name=user_name, reason=reason or "json fallback"))
                            _tool_used.add("feel")
                            logger.info(f"감정(JSON 폴백): {changes} | {reason}")
                        # response 처리
                        if response_val == "ignore":
                            _meta["intentional_silence"] = True
                        # reaction 처리
                        if reaction_val and not _meta.get("reaction"):
                            _fb_emojis = _extract_emojis(reaction_val)
                            if _fb_emojis:
                                _meta["reaction"] = reaction_val
                                logger.info(f"이모지 리액션(JSON 폴백): {reaction_val}")
                    except Exception as e:
                        logger.warning(f"feel JSON 파싱 실패: {e}")
                if json_match:
                    text = (text[:json_match.start()] + text[json_match.end():]).strip()
                # [mood:XX] 태그 제거 (v3 레거시)
                text = re.sub(r'\[mood:[+-]?\d+\]', '', text).strip()
                return text

            reply = _strip_feeling(reply)

            # 텍스트에 함수 호출 패턴이 섞여 있을 때 감지 후 실행
            _had_inline_function = False
            _TOOL_NAMES = {
                "search", "deliver", "memorize", "forget",
                "web_search", "memorize_alias", "forget_alias",
                "recognize_media", "recognize_link", "attach", "feel",
            }
            _POSITIONAL_MAP = {
                "deliver": "file_id",
                "attach": "media_id",
                "recognize_media": "attachment_index",
                "recognize_link": "url",
                "search": "keyword",
                "web_search": "query",
                "memorize": "content",
                "forget": "keyword",
                "forget_alias": "alias_id",
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
                    _had_inline_function = True

                    # args 파싱
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

                    if _tool_name in ("search", "memorize"):
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

                        if _tool_data.get("_action") == "deliver":
                            save_path = os.path.join(FILES_DIR, _tool_data["stored_name"])
                            if os.path.exists(save_path):
                                file_to_send = discord.File(save_path, filename=_tool_data["filename"])
                                await self.library_db.increment_download(_tool_data["file_id"])
                        elif _tool_data.get("_action") == "attach":
                            save_path = os.path.join(MEDIA_DIR, _tool_data["stored_name"])
                            if os.path.exists(save_path):
                                file_to_send = discord.File(save_path, filename=_tool_data["filename"])
                        elif _tool_data.get("_action") == "share_url":
                            _shared = _tool_data.get("url", "")
                            if _shared:
                                _meta.setdefault("shared_urls", []).append(_shared)
                    except Exception as _e:
                        logger.warning(f"인라인 함수 실행 실패 ({_tool_name}): {_e}")

                    # 함수 호출 제거, 남은 텍스트만 사용
                    reply = _before

            if not reply:
                if history and history[-1].role == "user":
                    history.pop()
                logger.info("[1차] 빈 응답 → 무응답")
                _meta["intentional_silence"] = True
                return "", file_to_send, _meta
            else:
                logger.info(f"[1차] 응답: {reply}")

            def _needs_retry(r):
                """빈 응답이거나 반복이면 리트라이 필요."""
                if not r:
                    return True
                is_rep = self._is_repeat(history, r)
                if is_rep:
                    logger.info(f"반복 감지: {r[:50]}")
                return is_rep

            clean_message = [types.Content(role="user", parts=[types.Part.from_text(text=user_content)])]

            if _needs_retry(reply):
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
                    if r:
                        r = _strip_feeling(r)
                    logger.info(f"[2차] 응답: {'빈 응답' if not r else r}")
                    if r and not self._is_repeat(history, r):
                        reply = r
                except Exception as e:
                    logger.warning(f"[2차] 실패: {e}")

            if _needs_retry(reply):
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
                    if r:
                        r = _strip_feeling(r)
                    logger.info(f"[3차] 응답: {'빈 응답' if not r else r}")
                    if r and not self._is_repeat(history, r):
                        reply = r
                except Exception as e:
                    logger.warning(f"[3차] 실패: {e}")

            if _needs_retry(reply):
                logger.warning("[포기] 응답 생성 실패")
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
                        elif _tool_data_r.get("_action") == "share_url":
                            _shared = _tool_data_r.get("url", "")
                            if _shared:
                                _meta.setdefault("shared_urls", []).append(_shared)
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
                        import re as _re
                        reply = _re.sub(r'\[mood:[+-]?\d+\]', '', reply).strip()
                        reply = _re.sub(r'feel\s*\([^)]*\)', '', reply).strip()
                        reply = _re.sub(r'/feel\s+[^\n]*', '', reply).strip()
                        reply = _re.sub(r'[\(\（]\s*feel\s*:[^)\）]*[\)\）]', '', reply).strip()
                        reply = _re.sub(r'\*\*\*\*', '', reply).strip()
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

    async def _build_reply_chain(self, message) -> tuple[list[str], list[str], list, object]:
        """답글 체인을 끝까지 거슬러 올라감. 10건 초과 시 앞5+뒤5. anchor도 반환."""
        chain = []
        seen_filenames = []
        chain_attachments = []
        raw_msgs = []
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
            raw_msgs.append(ref)
            current = ref

        # extras를 동시에 조회
        if raw_msgs:
            extras_list = await asyncio.gather(*(self._extract_extras(m) for m in raw_msgs))
        else:
            extras_list = []

        for ref, extras in zip(raw_msgs, extras_list):
            if self.user and ref.author.id == self.user.id:
                name = self.persona.name
            else:
                name = f"{ref.author.display_name}(<@{ref.author.id}>)"
            content = ref.content[:150]
            if extras:
                content = f"{content} {extras}" if content else extras
            for att in ref.attachments:
                seen_filenames.append(att.filename)
                chain_attachments.append(att)
            chain.append(f"{name}: {content}")

        anchor = raw_msgs[-1] if raw_msgs else message
        chain.reverse()

        if len(chain) > 10:
            head = chain[:5]
            tail = chain[-5:]
            chain = head + [f"... ({len(chain) - 10}건 생략) ..."] + tail

        return chain, seen_filenames, chain_attachments, anchor

    async def _build_pre_context(self, message, limit=10, anchor=None) -> list[str]:
        """답글 체인 시작점 직전 또는 멘션 직전 메시지들. anchor는 _build_reply_chain에서 받음."""
        if anchor is None:
            anchor = message

        msgs = [msg async for msg in anchor.channel.history(limit=limit, before=anchor)]
        if not msgs:
            return []
        extras_list = await asyncio.gather(*(self._extract_extras(m) for m in msgs))
        lines = []
        for msg, extras in zip(msgs, extras_list):
            if self.user and msg.author.id == self.user.id:
                name = self.persona.name
            else:
                name = f"{msg.author.display_name}(<@{msg.author.id}>)"
            content = msg.content[:150]
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