"""
Layer 01: Perception (인식)
맥락 수집 + Gemini API 호출로 상황 분석 + 검색/인식 도구 실행.
결과를 Execution과 Character에 넘긴다.
"""

import os
import json
import logging
from datetime import datetime as dt
from google.genai import types
from config import ADMIN_IDS, LIGHTNING_ADDRESS, AI_MAX_OUTPUT_TOKENS, GEMINI_API_KEY, GEMINI_MODEL, MEDIA_DIR, TEMP_L1

import importlib as _il
_btc = _il.import_module("librarian.layers.02_execution.bitcoin_data")
_tools = _il.import_module("librarian.layers.02_execution.tools")
execute_tool = _tools.execute_tool
parse_url = _tools.parse_url

logger = logging.getLogger("AILibrarian")

# L1 도구 선언 (로컬 검색 + 인식)
perception_declarations = [
    types.FunctionDeclaration(
        name="search",
        description="로컬 통합 검색. 지식, 기억, 도서(file_id), 미디어(media_id), URL(url_id), 웹 캐시를 검색한다. 질문이 오면 먼저 확인. 유저가 파일이나 사진을 요청하면 이걸로 찾아.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "keyword": types.Schema(type="STRING", description="검색 키워드"),
            },
            required=["keyword"],
        ),
    ),
    types.FunctionDeclaration(
        name="recognize_media",
        description="첨부된 이미지의 내용을 확인한다. 유저가 이미지를 보내면서 물어보면 사용. 여러 개면 인덱스를 배열로.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "indices": types.Schema(type="ARRAY", items=types.Schema(type="INTEGER"), description="첨부파일 번호 배열 (0부터)"),
            },
            required=["indices"],
        ),
    ),
    types.FunctionDeclaration(
        name="recognize_link",
        description="URL의 웹페이지 내용을 확인한다. 유저가 링크를 보내면서 물어보면 사용. 여러 개면 URL을 배열로.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "urls": types.Schema(type="ARRAY", items=types.Schema(type="STRING"), description="확인할 URL 배열"),
            },
            required=["urls"],
        ),
    ),
    types.FunctionDeclaration(
        name="recognize_file",
        description="첨부된 TXT, EPUB, MD 파일의 내용을 확인한다. 문서 파일을 보내면서 물어보면 사용. 여러 개면 인덱스를 배열로. 백그라운드 처리.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "indices": types.Schema(type="ARRAY", items=types.Schema(type="INTEGER"), description="첨부파일 번호 배열 (0부터)"),
            },
            required=["indices"],
        ),
    ),
]
perception_tools = [types.Tool(function_declarations=perception_declarations)]


async def gather_context(self, user_id: str, user_name: str,
                         guild=None, reply_chain: list[str] = None,
                         anchor_context: list[str] = None,
                         recent_context: list[str] = None,
                         channel_id: str = None,
                         shared_ctx: dict = None) -> str:
    """공통 컨텍스트(shared_ctx) + 외부 데이터에서 raw context 수집. DB 직접 조회 없음."""
    import re as _re
    import zoneinfo
    ctx = shared_ctx or {}
    parts = []

    # 상황 정보
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

    admin_names = []
    if guild:
        for aid in ADMIN_IDS:
            try:
                member = guild.get_member(int(aid))
                if member:
                    admin_names.append(member.display_name)
            except Exception:
                pass
    role = "주인 (도서관 관리자)" if user_id in ADMIN_IDS else "일반 방문자"
    situation = f"## 상황\n현재: {time_str}\n대화 상대: @{user_name} ({role})"
    if admin_names:
        situation += f"\n도서관 주인: {', '.join(f'@{n}' for n in admin_names)}"
    if LIGHTNING_ADDRESS:
        situation += f"\n후원 라이트닝 주소: {LIGHTNING_ADDRESS}"
    parts.append(situation)

    # 비트코인 현황
    btc_block = _btc.get_prompt_block()
    if btc_block:
        parts.append(btc_block)

    # 감정 상태 (shared_ctx에서 읽음)
    bot_emo = ctx.get("bot_emotion", {})
    bot_lines = []
    bot_lines.append(f"self_mood:{bot_emo.get('self_mood', 50):.1f}")
    bot_lines.append(f"self_energy:{bot_emo.get('self_energy', 50):.1f}")
    bot_lines.append(f"server_vibe:{bot_emo.get('server_vibe', 50):.1f}")
    bot_lines.append(f"fullness:{bot_emo.get('fullness', 50):.0f}")
    bot_lines.append(f"hydration:{bot_emo.get('hydration', 50):.0f}")

    user_lines = []
    user_emo = ctx.get("user_emotion")
    if user_emo:
        user_lines.append(
            f"@{user_name}: "
            + " ".join(f"{k}:{user_emo[k]:.1f}" for k in self.librarian_db.USER_AXES)
            + f" (대화 {user_emo['interaction_count']}회)")
    else:
        user_lines.append(f"@{user_name}: 첫 방문 (수치 없음)")

    chain_user_ids = set()
    if reply_chain:
        for line in reply_chain:
            m = _re.search(r'@(\S+)', line)
            if m:
                name = m.group(1)
                uid = self._mention_map.get(name)
                if uid and uid != user_id:
                    chain_user_ids.add(uid)
    if chain_user_ids:
        chain_emos = await self.librarian_db.get_user_emotions_bulk(chain_user_ids)
        for uid, emo in chain_emos.items():
            name = emo.get("user_name", uid)
            user_lines.append(
                f"@{name}: " + " ".join(f"{k}:{emo[k]:.1f}" for k in self.librarian_db.USER_AXES))

    emo_block = "## 감정 수치 (50이 중립, 0-100)\n"
    emo_block += "기본 상태 (봇 전체): " + " ".join(bot_lines) + "\n"
    emo_block += "유저별:\n" + "\n".join(f"  {l}" for l in user_lines)
    parts.append(emo_block)

    # 경제 상태 (공통 컨텍스트 — L1/L2/L3 동일)
    from library.cogs.shop import SHOP_PAGE1, SHOP_PAGE2, SHOP_ITEMS
    bot_balance = ctx.get("balance", 0)
    normal_items = ", ".join(f"{i['emoji']}{i['name']}({i['id']},{i['price']}sat)" for i in SHOP_PAGE1)
    special_items = ", ".join(f"{i['emoji']}{i['name']}({i['id']},{i['price']}sat)" for i in SHOP_PAGE2)
    affordable = [f"{i['emoji']}{i['name']}({i['id']})" for i in SHOP_ITEMS if i["price"] <= bot_balance]
    econ_block = f"## 내 경제\n잔고: {bot_balance} sat\n"
    econ_block += f"일반 아이템: {normal_items}\n"
    econ_block += f"이상한 아이템: {special_items}\n"
    if affordable:
        econ_block += f"선물 가능: {', '.join(affordable)}"
    else:
        econ_block += "선물 가능한 아이템 없음 (잔고 부족)"
    parts.append(econ_block)

    # 이전 피드백 3종 (shared_ctx에서)
    fb_parts = []
    if ctx.get("feedback"):
        fb_parts.append(f"[유저] {ctx['feedback']}")
    if ctx.get("channel_feedback"):
        fb_parts.append(f"[채널] {ctx['channel_feedback']}")
    if ctx.get("global_feedback"):
        fb_parts.append(f"[전체] {ctx['global_feedback']}")
    if fb_parts:
        parts.append("## 이전 피드백 (최우선)\n" + "\n".join(fb_parts))
        logger.info(f"[Perception] 이전 피드백 로드 ({len(fb_parts)}건)")

    # 대화 요약 (shared_ctx에서)
    user_summary = ctx.get("user_summary")
    if user_summary:
        parts.append(f"## @{user_name}과의 대화 요약\n{user_summary}")
    channel_summary = ctx.get("channel_summary")
    if channel_summary:
        parts.append(f"## 이 채널 흐름 요약\n{channel_summary}")

    # 도서관 카탈로그 + 기억 (공통 컨텍스트)
    catalog = ctx.get("catalog", "")
    if catalog:
        parts.append(f"## 도서관 목록\n{catalog}")
    memories = ctx.get("memories")
    if memories:
        memories_text = memories[0] if isinstance(memories, tuple) else memories
        if memories_text:
            parts.append(f"## 기억\n{memories_text}")

    # 답글 대상 주변 맥락
    if anchor_context:
        parts.append("## 답글 대상 주변 맥락\n" + "\n".join(anchor_context))

    # 최근 채널 대화
    if recent_context:
        parts.append("## 최근 채널 대화\n" + "\n".join(recent_context))

    # 답글 흐름
    if reply_chain:
        parts.append("## 답글 흐름\n" + "\n".join(reply_chain))

    result = "\n\n".join(parts)
    # 동적 맥락만 로깅
    _dynamic = {"답글 흐름", "답글 대상 주변 맥락", "최근 채널 대화"}
    _log = []
    _static = []
    for p in parts:
        if not p.startswith("##"):
            continue
        title = p.split("\n")[0].strip("# ")
        if title in _dynamic:
            _log.append(f"  [{title}]")
            for line in p.split("\n")[1:]:
                if line.strip():
                    _log.append(f"    | {line.strip()}")
        else:
            _static.append(title)
    logger.info(f"[gather_context] {len(result)}자, 고정: {_static}")
    if _log:
        logger.info(f"[gather_context] 동적 맥락:\n" + "\n".join(_log))
    return result


SPONTANEOUS_RESPONSE_PROMPT = """## 응답 판정

멘션 없는 메시지다. 먼저 판정 한 줄을 출력해.

판정 형식:
decide_to_ignore — 사유
decide_to_pause — 사유
decide_to_reply — 사유
decide_to_reply_to — 사유

기본값은 ignore다. 대부분의 메시지는 너한테 하는 말이 아니다.

ignore:
- 다른 사람을 부르고 있다 (다른 이름 언급)
- 사람들끼리 대화 중이다
- 혼잣말, 감탄, 욕설, 감정 분출
- 너와 관련 없는 주제
다른 사람 이름이 언급됐으면 ignore.

pause:
- 메시지가 끊어져 있다 (문장 미완결)

reply (전부 충족):
- 너를 지칭하거나 너한테 말하고 있다는 명확한 근거
- 다른 사람을 부르고 있지 않다
- 말이 끝났다
근거 없으면 ignore.

ignore/pause면 판정 한 줄만 쓰고 끝내. 분석하지 마.
reply/reply_to면 판정 뒤에 평소대로 관찰과 분석을 이어서 해."""


async def run_perception(self, user_id: str, user_name: str,
                         user_text: str, raw_context: str,
                         history: list = None,
                         attachments: list = None,
                         seen_filenames: list = None,
                         is_spontaneous: bool = False,
                         thinking_level: str = "minimal") -> str:
    """raw context를 Gemini에 보내서 상황 분석 + 검색/인식 도구 실행. 1회 호출."""
    import asyncio

    sys_parts = []
    if self.persona.perception_text:
        sys_parts.append(self.persona.perception_text)
    if is_spontaneous:
        sys_parts.append(SPONTANEOUS_RESPONSE_PROMPT)
    if raw_context:
        sys_parts.append(raw_context)
    system_prompt = "\n\n".join(p for p in sys_parts if p)

    _level_map = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=perception_tools,
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=TEMP_L1,
        thinking_config=types.ThinkingConfig(thinking_level=_level_map.get(thinking_level, "MINIMAL")),
    )

    if user_text:
        user_content = f"{user_name}: {user_text}"
    else:
        user_content = f"({user_name}이 빈 멘션을 보냈다.)"

    # 채널별 히스토리 + 이번 유저 메시지
    contents = list(history) if history else []
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))

    from librarian.core import MODEL_L1
    logger.info(f"[Perception] API 호출 (model={MODEL_L1}, thinking={thinking_level}, 히스토리={len(contents)-1}턴)")
    response = await self._call_gemini(contents, config, model=MODEL_L1)

    # 1회 응답에서 텍스트 + function_call 모두 추출
    result = ""
    tool_results = []

    if not response or not response.candidates:
        logger.warning("[Perception] API 응답 없음 (no candidates)")
    elif not response.candidates[0].content or not response.candidates[0].content.parts:
        finish = getattr(response.candidates[0], "finish_reason", "unknown")
        logger.warning(f"[Perception] 빈 응답 (finish_reason: {finish})")
    else:
        for part in response.candidates[0].content.parts:
            if part.text and part.text.strip():
                result = part.text.strip()

            if not part.function_call:
                continue
            fc = part.function_call
            logger.info(f"[Perception] 도구: {fc.name}({fc.args})")

            # search
            if fc.name == "search":
                tool_args = dict(fc.args) if fc.args else {}
                tool_args["_user_id"] = user_id
                tool_args["_user_name"] = user_name
                search_result = await execute_tool(
                    self.library_db, self.librarian_db, "search", tool_args)
                logger.info(f"[Perception] search 결과: {search_result[:200]}")
                tool_results.append(f"검색 결과:\n{search_result}")

            # recognize_media
            elif fc.name == "recognize_media":
                fc_args = dict(fc.args) if fc.args else {}
                indices = fc_args.get("indices") or [fc_args.get("attachment_index", 0)]
                current_attachments = attachments or []
                for att_idx in indices:
                    att_idx = int(att_idx)
                    media_result = ""
                    media_id = None
                    if att_idx < len(current_attachments):
                        att = current_attachments[att_idx]
                        ct = att.content_type or ""
                        cached = None
                        data = None
                        file_hash = None
                        if ct.startswith("image/"):
                            data = await att.read()
                            import hashlib
                            file_hash = hashlib.sha256(data).hexdigest()
                            cached = await self.librarian_db.get_media_by_hash(file_hash)

                        if cached:
                            media_result = cached["result"]
                            media_id = cached.get("id")
                        elif ct.startswith("image/") or ct == "application/pdf":
                            try:
                                media_parts = [
                                    types.Part.from_bytes(data=data, mime_type=ct),
                                    types.Part.from_text(text="3-4줄로 핵심만 설명해."),
                                ]
                                media_config = types.GenerateContentConfig(
                                    max_output_tokens=AI_MAX_OUTPUT_TOKENS, temperature=1.0,
                                    media_resolution="MEDIA_RESOLUTION_LOW")
                                media_response = await self._call_gemini(
                                    [types.Content(role="user", parts=media_parts)], media_config)
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
                                    except Exception:
                                        stored_name = None
                                    media_id = await self.librarian_db.save_media_result(
                                        att.filename, media_result, user_name=user_name,
                                        uploader=user_name, stored_name=stored_name, file_hash=file_hash)
                            except Exception as e:
                                logger.warning(f"[Perception] 미디어 인식 실패 ({att_idx}): {e}")
                        elif ct.startswith("video/"):
                            # 영상 → ffmpeg 첫 프레임 추출 → 이미지 인식
                            import shutil
                            if not shutil.which("ffmpeg"):
                                media_result = "영상 인식 불가 (ffmpeg 미설치)"
                            else:
                                try:
                                    data = await att.read()
                                    import tempfile, subprocess
                                    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(att.filename)[1] or ".mp4", delete=False) as tmp_video:
                                        tmp_video.write(data)
                                        tmp_video_path = tmp_video.name
                                    tmp_thumb_path = tmp_video_path + ".jpg"
                                    subprocess.run(
                                        ["ffmpeg", "-i", tmp_video_path, "-vframes", "1", "-q:v", "2", tmp_thumb_path, "-y"],
                                        capture_output=True, timeout=10)
                                    if os.path.exists(tmp_thumb_path):
                                        with open(tmp_thumb_path, "rb") as f:
                                            thumb_data = f.read()
                                        media_parts = [
                                            types.Part.from_bytes(data=thumb_data, mime_type="image/jpeg"),
                                            types.Part.from_text(text="이 영상의 첫 장면이야. 3-4줄로 설명해."),
                                        ]
                                        media_config = types.GenerateContentConfig(
                                            max_output_tokens=AI_MAX_OUTPUT_TOKENS, temperature=1.0,
                                            media_resolution="MEDIA_RESOLUTION_LOW")
                                        media_response = await self._call_gemini(
                                            [types.Content(role="user", parts=media_parts)], media_config)
                                        media_result = self._extract_reply(media_response)
                                        if media_result:
                                            media_result = f"[영상 썸네일] {media_result}"
                                    for p in [tmp_video_path, tmp_thumb_path]:
                                        try:
                                            os.remove(p)
                                        except Exception:
                                            pass
                                except Exception as e:
                                    logger.warning(f"[Perception] 영상 썸네일 추출 실패 ({att_idx}): {e}")
                        else:
                            media_result = f"이 파일 형식({ct})은 인식할 수 없어."
                    else:
                        media_result = "첨부파일이 없어."
                    id_tag = f" (media_id:{media_id})" if media_id else ""
                    tool_results.append(f"미디어 인식 ({att_idx}){id_tag}: {media_result or '인식 실패'}")

            # recognize_link
            elif fc.name == "recognize_link":
                fc_args = dict(fc.args) if fc.args else {}
                urls = fc_args.get("urls") or [fc_args.get("url", "")]
                for url in urls:
                    if not url:
                        continue
                    link_result = ""
                    url_id = None
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
                            img_config = types.GenerateContentConfig(max_output_tokens=500, temperature=1.0)
                            img_response = await self._call_gemini(
                                [types.Content(role="user", parts=img_parts)], img_config)
                            link_result = self._extract_reply(img_response)
                            if link_result:
                                url_id = await self.librarian_db.save_url_result(
                                    normalized, url, link_result, user_name=user_name, status="done")
                        except Exception as e:
                            logger.warning(f"[Perception] 이미지 URL 인식 실패 ({url}): {e}")

                    # 유튜브/일반 URL: 백그라운드로 넘김

                    # 일반 URL: 백그라운드로 넘김

                    if not link_result:
                        cached = await self.librarian_db.get_url_by_normalized(normalized)
                        if cached:
                            if cached.get("status") == "pending":
                                link_result = "아직 읽는 중."
                            elif cached.get("status") == "failed":
                                await self.librarian_db.update_url_result(normalized, "", status="pending")
                                asyncio.create_task(self._recognize_url_background(parsed, user_name))
                                link_result = "방금 읽기 시작했어."
                            else:
                                link_result = cached["result"]
                                url_id = cached.get("id")

                    if not link_result:
                        await self.librarian_db.save_url_result(
                            normalized, url, "", user_name=user_name, status="pending")
                        asyncio.create_task(self._recognize_url_background(parsed, user_name))
                        link_result = "방금 읽기 시작했어."

                    id_tag = f" (url_id:{url_id})" if url_id else ""
                    tool_results.append(f"링크 인식 ({url}){id_tag}: {link_result or '인식 실패'}")

            # recognize_file (TXT, EPUB, MD — 캐시 있으면 즉시, 없으면 백그라운드)
            elif fc.name == "recognize_file":
                fc_args = dict(fc.args) if fc.args else {}
                indices = fc_args.get("indices") or [0]
                current_attachments = attachments or []
                _allowed_exts = (".txt", ".epub", ".md")
                for att_idx in indices:
                    att_idx = int(att_idx)
                    file_result = ""
                    media_id = None
                    if att_idx < len(current_attachments):
                        att = current_attachments[att_idx]
                        ext = os.path.splitext(att.filename)[1].lower()

                        if ext not in _allowed_exts:
                            file_result = f"이 파일 형식({ext})은 지원하지 않는다. (txt, epub, md만 가능)"
                        else:
                            # 해시 → 파일명 순서로 캐시 조회
                            data = await att.read()
                            import hashlib
                            file_hash = hashlib.sha256(data).hexdigest()
                            cached = await self.librarian_db.get_media_by_hash(file_hash)
                            if cached and cached.get("result"):
                                file_result = cached["result"]
                                media_id = cached.get("id")
                            else:
                                # 파일 저장 후 백그라운드 처리
                                try:
                                    os.makedirs(MEDIA_DIR, exist_ok=True)
                                    import uuid
                                    stored_name = f"{uuid.uuid4().hex}{ext}"
                                    with open(os.path.join(MEDIA_DIR, stored_name), "wb") as mf:
                                        mf.write(data)
                                    media_id = await self.librarian_db.save_media_result(
                                        att.filename, "", user_name=user_name,
                                        uploader=user_name, stored_name=stored_name, file_hash=file_hash)
                                    # 백그라운드에서 Gemini 인식
                                    asyncio.create_task(self._recognize_file_background(
                                        att.filename, stored_name, ext, user_name))
                                    file_result = "읽기 시작했어."
                                except Exception as e:
                                    logger.warning(f"[Perception] 문서 저장 실패 ({att_idx}): {e}")
                                    file_result = "파일 저장에 실패했어."
                    else:
                        file_result = "첨부파일이 없어."
                    id_tag = f" (media_id:{media_id})" if media_id else ""
                    tool_results.append(f"문서 인식 ({att_idx}){id_tag}: {file_result}")

    # 분석 텍스트 + 도구 결과 합침
    if tool_results:
        tool_block = "\n\n".join(tool_results)
        if result:
            result = f"{result}\n\n## 도구 결과\n{tool_block}"
        else:
            result = f"## 도구 결과\n{tool_block}"

    if result:
        logger.info(f"[Perception] 분석 완료 ({len(result)}자):\n{result}")
    else:
        logger.warning("[Perception] 분석 실패 — raw context 직접 사용")
        result = raw_context

    return result
