"""
Layer 01: Perception (인식)
맥락 수집 + Gemini API 호출로 상황 분석 + 검색/인식 도구 실행.
결과를 Functioning과 Character에 넘긴다.
"""

import os
import json
import logging
from datetime import datetime as dt
from google.genai import types
from config import ADMIN_IDS, LIGHTNING_ADDRESS, AI_MAX_OUTPUT_TOKENS, GEMINI_API_KEY, GEMINI_MODEL, MEDIA_DIR, TEMP_L1

import importlib as _il
_btc = _il.import_module("librarian.layers.02_functioning.bitcoin_data")
_tools = _il.import_module("librarian.layers.02_functioning.tools")
execute_tool = _tools.execute_tool
parse_url = _tools.parse_url

logger = logging.getLogger("AILibrarian")

# L1 검색/인식 도구 선언
perception_declarations = [
    types.FunctionDeclaration(
        name="search",
        description="비트코인/경제/철학 지식과 유저 기억을 검색한다. 질문이 오면 먼저 이걸로 확인해. 뉴스 헤드라인이나 도시 날씨도 검색 가능.",
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
        description="첨부된 이미지나 PDF의 내용을 확인한다. 유저가 이미지나 파일을 보내면서 '이거 뭐야', '읽어봐' 같은 요청을 하면 사용.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "attachment_index": types.Schema(type="INTEGER", description="첨부파일 번호 (0부터)"),
            },
            required=["attachment_index"],
        ),
    ),
    types.FunctionDeclaration(
        name="recognize_link",
        description="URL의 웹페이지 내용을 확인한다. 유저가 링크를 보내면서 '이거 뭐야', '요약해줘' 같은 요청을 하면 사용.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "url": types.Schema(type="STRING", description="확인할 URL"),
            },
            required=["url"],
        ),
    ),
]
perception_tools = [types.Tool(function_declarations=perception_declarations)]


async def gather_context(self, user_id: str, user_name: str,
                         guild=None, reply_chain: list[str] = None,
                         anchor_context: list[str] = None,
                         recent_context: list[str] = None,
                         channel_id: str = None) -> str:
    """DB + 외부 데이터에서 raw context 수집. 순수 코드, API 호출 없음."""
    import re as _re
    import zoneinfo
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
    situation = f"## 상황\n현재: {time_str}\n대화 상대: {user_name} ({role})"
    if admin_names:
        situation += f"\n도서관 주인: {', '.join(admin_names)}"
    if LIGHTNING_ADDRESS:
        situation += f"\n후원 라이트닝 주소: {LIGHTNING_ADDRESS}"
    parts.append(situation)

    # 비트코인 현황
    btc_block = _btc.get_prompt_block()
    if btc_block:
        parts.append(btc_block)

    # 감정 상태: 기본 상태 + 유저별 분리
    bot_emo = await self.librarian_db.get_bot_emotion()

    # 기본 상태 (봇 전체)
    bot_lines = []
    bot_lines.append(f"self_mood:{bot_emo.get('self_mood', 50):.1f}")
    bot_lines.append(f"self_energy:{bot_emo.get('self_energy', 50):.1f}")
    bot_lines.append(f"server_vibe:{bot_emo.get('server_vibe', 50):.1f}")
    bot_lines.append(f"fullness:{bot_emo.get('fullness', 50):.0f}")
    bot_lines.append(f"hydration:{bot_emo.get('hydration', 50):.0f}")

    # 유저별 상태
    user_lines = []
    user_emo = await self.librarian_db.get_user_emotion(user_id)
    if user_emo:
        user_lines.append(
            f"{user_name}: "
            + " ".join(f"{k}:{user_emo[k]:.1f}" for k in self.librarian_db.USER_AXES)
            + f" (대화 {user_emo['interaction_count']}회)")
    else:
        user_lines.append(f"{user_name}: 첫 방문 (수치 없음)")

    chain_user_ids = set()
    if reply_chain:
        for line in reply_chain:
            m = _re.search(r'<@(\d+)>', line)
            if m and m.group(1) != user_id:
                chain_user_ids.add(m.group(1))
    if chain_user_ids:
        chain_emos = await self.librarian_db.get_user_emotions_bulk(chain_user_ids)
        for uid, emo in chain_emos.items():
            name = emo.get("user_name", uid)
            user_lines.append(
                f"{name}: " + " ".join(f"{k}:{emo[k]:.1f}" for k in self.librarian_db.USER_AXES))

    emo_block = "## 감정 수치 (50이 중립, 0-100)\n"
    emo_block += "기본 상태 (봇 전체): " + " ".join(bot_lines) + "\n"
    emo_block += "유저별:\n" + "\n".join(f"  {l}" for l in user_lines)
    parts.append(emo_block)

    # 경제 상태 + 아이템 목록
    from library.cogs.shop import SHOP_PAGE1, SHOP_PAGE2
    bot_id = str(self.user.id) if self.user else ""
    bot_balance = await self.library_db.get_balance(bot_id) if bot_id else 0
    item_names = ", ".join(f"{i['emoji']}{i['name']}({i['price']}sat)" for i in SHOP_PAGE1)
    special_names = ", ".join(f"{i['emoji']}{i['name']}({i['price']}sat)" for i in SHOP_PAGE2)
    parts.append(f"## 내 경제\n잔고: {bot_balance} sat\n일반 아이템: {item_names}\n이상한 아이템: {special_names}")

    # 이전 피드백
    prev_feedback = await self.librarian_db.get_feedback(user_id)
    if prev_feedback:
        parts.append(f"## 이전 피드백\n{prev_feedback}")
        logger.info(f"[Perception] 이전 피드백 로드 ({len(prev_feedback)}자)")

    # 대화 요약 (장기 맥락)
    user_summary = await self.librarian_db.get_user_summary(user_id)
    if user_summary:
        parts.append(f"## {user_name}과의 대화 요약\n{user_summary}")
    if channel_id:
        channel_summary = await self.librarian_db.get_channel_summary(channel_id)
        if channel_summary:
            parts.append(f"## 이 채널 흐름 요약\n{channel_summary}")

    # 답글 대상 주변 맥락
    if anchor_context:
        parts.append("## 답글 대상 주변 맥락\n" + "\n".join(anchor_context))

    # 최근 채널 대화
    if recent_context:
        parts.append("## 최근 채널 대화\n" + "\n".join(recent_context))

    # 답글 흐름
    if reply_chain:
        parts.append("## 답글 흐름\n" + "\n".join(reply_chain))

    return "\n\n".join(parts)


SPONTANEOUS_RESPONSE_PROMPT = """## 응답 판정

멘션 없는 메시지다. 현재 메시지만 보고 판정해. 관찰이나 분석보다 판정이 먼저다.

사람들은 메시지를 끊어서 친다. 한 문장을 여러 메시지로 나눠 보낸다.
말이 끝났는지 확신이 없으면 wait.

"decide_to_pause" — 기다린다. 판정만 쓰고 끝. 관찰 생략.
"decide_to_reply" — 말이 확실히 끝났고, 참여할 만할 때만."""


async def run_perception(self, user_id: str, user_name: str,
                         user_text: str, raw_context: str,
                         history: list = None,
                         attachments: list = None,
                         seen_filenames: list = None,
                         is_spontaneous: bool = False) -> str:
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

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=perception_tools,
        max_output_tokens=AI_MAX_OUTPUT_TOKENS,
        temperature=TEMP_L1,
    )

    if user_text:
        user_content = f"{user_name}: {user_text}"
    else:
        user_content = f"({user_name}이 빈 멘션을 보냈다.)"

    # 채널별 히스토리 + 이번 유저 메시지
    contents = list(history) if history else []
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))

    logger.info(f"[Perception] API 호출 (히스토리={len(contents)-1}턴)")
    response = await self._call_gemini(contents, config)

    # 1회 응답에서 텍스트 + function_call 모두 추출
    result = ""
    tool_results = []

    if response and response.candidates and response.candidates[0].content.parts:
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
                att_idx = (dict(fc.args) if fc.args else {}).get("attachment_index", 0)
                media_result = ""
                current_attachments = attachments or []
                if att_idx < len(current_attachments):
                    att = current_attachments[att_idx]
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
                        media_result = cached["result"]
                    elif ct.startswith("image/") or ct == "application/pdf":
                        try:
                            media_parts = [
                                types.Part.from_bytes(data=data, mime_type=ct),
                                types.Part.from_text(text="3-4줄로 핵심만 설명해."),
                            ]
                            media_config = types.GenerateContentConfig(
                                max_output_tokens=AI_MAX_OUTPUT_TOKENS, temperature=0.5)
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
                                await self.librarian_db.save_media_result(
                                    att.filename, media_result, user_name=user_name,
                                    uploader=user_name, stored_name=stored_name, file_hash=file_hash)
                        except Exception as e:
                            logger.warning(f"[Perception] 미디어 인식 실패: {e}")
                    else:
                        media_result = f"이 파일 형식({ct})은 인식할 수 없어."
                else:
                    media_result = "첨부파일이 없어."
                tool_results.append(f"미디어 인식: {media_result or '인식 실패'}")

            # recognize_link
            elif fc.name == "recognize_link":
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
                    except Exception as e:
                        logger.warning(f"[Perception] 이미지 URL 인식 실패 ({url}): {e}")

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

                if not link_result:
                    await self.librarian_db.save_url_result(
                        normalized, url, "", user_name=user_name, status="pending")
                    asyncio.create_task(self._recognize_url_background(parsed, user_name))
                    link_result = "방금 읽기 시작했어."

                tool_results.append(f"링크 인식({url}): {link_result or '인식 실패'}")

    # 분석 텍스트 + 도구 결과 합침
    if tool_results:
        tool_block = "\n\n".join(tool_results)
        if result:
            result = f"{result}\n\n## 도구 결과\n{tool_block}"
        else:
            result = f"## 도구 결과\n{tool_block}"

    if result:
        logger.info(f"[Perception] 분석 완료 ({len(result)}자): {result[:150]}")
    else:
        logger.warning("[Perception] 분석 실패 — raw context 직접 사용")
        result = raw_context

    return result
