"""
AI 사서봇 - Gemini function calling으로 도서관 기능 + 잡담
"""

import os
import json
import discord
import logging
from collections import deque
from datetime import date, timedelta
from google import genai
from google.genai import types
from google.genai.errors import ClientError

from database import Database
from config import UPLOAD_DIR, ADMIN_IDS
from ai.persona import Persona
from ai.tools import library_tools, execute_tool

logger = logging.getLogger("AILibrarian")

MODEL = "gemini-2.5-flash-lite"
CHANNEL_BUFFER_SIZE = 50
CONTEXT_WINDOW_SIZE = 20  # Gemini에 넘길 최근 채널 메시지 수


class AILibrarianBot(discord.Client):
    def __init__(self, persona: Persona, gemini_api_keys: list[str]):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.persona = persona
        self.db = Database()
        self._gemini_clients = [genai.Client(api_key=k) for k in gemini_api_keys]
        self._client_index = 0
        self._dead_until: dict[int, date] = {}  # client index -> 일일 한도 만료일
        self.chat_histories: dict[int, list] = {}  # channel_id -> Gemini 대화 턴
        self.channel_buffers: dict[int, deque] = {}  # channel_id -> 최근 메시지 버퍼
        self.global_buffer: deque = deque(maxlen=CHANNEL_BUFFER_SIZE)  # 전체 채널 통합 버퍼
        self._ready = False

        # 기억 트리거 로드
        triggers_path = os.path.join(os.path.dirname(__file__), "ai", "memory_triggers.txt")
        if os.path.exists(triggers_path):
            with open(triggers_path, encoding="utf-8") as f:
                self._memory_triggers = [line.strip() for line in f if line.strip()]
        else:
            self._memory_triggers = ["기억해"]

    def _get_buffer(self, channel_id: int) -> deque:
        if channel_id not in self.channel_buffers:
            self.channel_buffers[channel_id] = deque(maxlen=CHANNEL_BUFFER_SIZE)
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

    def _build_context(self, channel_id: int, user_id: str) -> dict:
        """맥락 4종을 조립해서 반환"""
        def _format(entries, include_channel=False):
            lines = []
            for msg in entries:
                name = self.persona.name if msg["is_bot"] else msg["user_name"]
                prefix = f"[#{msg['channel_name']}] " if include_channel else ""
                lines.append(f"{prefix}{name}: {msg['text']}")
            return "\n".join(lines)

        # 1. 현재 채널 최근 대화
        ch_buf = list(self._get_buffer(channel_id))[-CONTEXT_WINDOW_SIZE:]
        channel_ctx = _format(ch_buf)

        # 2. 전체 채널 통합 최근 대화 (현재 채널 제외)
        global_ctx = _format(
            [m for m in list(self.global_buffer)[-CONTEXT_WINDOW_SIZE:] if m["channel_name"] != (ch_buf[0]["channel_name"] if ch_buf else "")],
            include_channel=True,
        )

        # 3. 말 건 유저의 최근 발언 (전체 채널에서)
        user_msgs = [m for m in self.global_buffer if m["user_id"] == user_id and not m["is_bot"]]
        user_ctx = _format(list(user_msgs)[-10:], include_channel=True)

        return {
            "channel": channel_ctx,
            "global": global_ctx,
            "user": user_ctx,
        }

    async def on_ready(self):
        await self.db.init()
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

        # 멘션이 아니면 여기서 끝
        if not self.user or self.user not in message.mentions:
            return

        # 멘션 제거해서 실제 메시지 추출
        for mention in [f"<@{self.user.id}>", f"<@!{self.user.id}>"]:
            text = text.replace(mention, "")
        text = text.strip()

        # 빈 멘션이면 빈 문자열 그대로 전달 (프롬프트에서 처리)
        if not text:
            text = ""

        async with message.channel.typing():
            reply_text, file_to_send = await self._ask_gemini(
                channel_id=message.channel.id,
                user_id=str(message.author.id),
                user_name=message.author.display_name,
                user_text=text,
            )

        # "기억해" 패턴 감지 → 코드에서 직접 저장 (flash-lite가 도구 호출을 못 하므로)
        if text and any(kw in text.lower() for kw in self._memory_triggers):
            # 멘션 태그 제거 후 저장
            clean_text = text
            if self.user:
                for tag in [f"@{self.persona.name}", f"<@{self.user.id}>", f"<@!{self.user.id}>"]:
                    clean_text = clean_text.replace(tag, "").strip()
            await self.db.save_user_memory(str(message.author.id), clean_text)
            logger.info(f"기억 자동 저장: {clean_text}")

        # 빈 응답(안전 필터 차단 등)이면 무시
        if not reply_text and not file_to_send:
            return

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
                          user_name: str, user_text: str) -> tuple[str, discord.File | None]:
        """Gemini에게 질문하고 응답 + 파일(있으면) 반환"""
        if channel_id not in self.chat_histories:
            self.chat_histories[channel_id] = []
        history = self.chat_histories[channel_id]

        # 시스템 프롬프트에 동적 맥락 삽입
        dynamic_prompt = self.persona.system_prompt

        # 맥락 4종 조립
        ctx = self._build_context(channel_id, user_id)
        if ctx["channel"]:
            dynamic_prompt += f"\n\n## 현재 채널 최근 대화\n{ctx['channel']}"
        if ctx["global"]:
            dynamic_prompt += f"\n\n## 다른 채널 최근 대화\n{ctx['global']}"
        if ctx["user"]:
            dynamic_prompt += f"\n\n## 이 유저의 최근 발언\n{ctx['user']}"

        # 대화 상대 정보
        role = "주인 (도서관 관리자)" if user_id in ADMIN_IDS else "일반 방문자"
        logger.info(f"대화 상대: {user_name} (ID: {user_id}) → {role}")
        dynamic_prompt += f"\n\n## 현재 대화 상대\n유저 이름: {user_name}\n유저 ID: {user_id}\n권한: {role}"

        # 저장된 기억 자동 로드
        common_mems = await self.db.recall_memories(5)
        user_mems = await self.db.recall_user_memories(user_id, 5)
        if common_mems or user_mems:
            mem_lines = []
            if common_mems:
                mem_lines.append("공통 기억:")
                for m in common_mems:
                    mem_lines.append(f"- {m['content']}")
            if user_mems:
                mem_lines.append(f"{user_name}에 대한 기억:")
                for m in user_mems:
                    mem_lines.append(f"- {m['content']}")
            dynamic_prompt += f"\n\n## 저장된 기억\n" + "\n".join(mem_lines)

        # 유저 메시지 구성
        if user_text:
            user_content = f"{user_name}: {user_text}"
        else:
            user_content = f"({user_name}이 빈 멘션을 보냈다.)"

        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_content)]))

        file_to_send = None

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
            """살아있는 키로 호출, 429 시 다음 키로 재시도 (최대 키 수 × 2)"""
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
                    if e.status == "RESOURCE_EXHAUSTED":
                        msg = str(e)
                        if "PerDay" in msg:
                            # 일일 한도: 내일까지 이 키 비활성화
                            self._dead_until[idx] = date.today() + timedelta(days=1)
                            logger.warning(f"키 #{idx} 일일 한도 초과, 내일까지 비활성화")
                        else:
                            logger.warning(f"키 #{idx} 분당 한도 초과, 다음 키로 재시도...")
                        last_err = e
                        continue
                    raise
            if last_err:
                raise last_err
            raise ClientError("모든 API 키 소진")

        try:
            response = _call_gemini(history)

            # function call 루프 (최대 5회 - 기억 조회+저장+도서관 조합)
            for _ in range(5):
                fc = None
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        fc = part.function_call
                        break
                if not fc:
                    break

                logger.info(f"도구 호출: {fc.name}({fc.args})")

                tool_args = dict(fc.args) if fc.args else {}
                if fc.name == "save_memory" and "user_id" not in tool_args:
                    tool_args["user_id"] = user_id
                if fc.name == "recall_memories" and "user_id" not in tool_args:
                    tool_args["user_id"] = user_id
                tool_result = await execute_tool(self.db, fc.name, tool_args)
                tool_data = json.loads(tool_result)

                # send_file 액션: 실제 파일 전송 준비
                if tool_data.get("_action") == "send_file":
                    save_path = os.path.join(UPLOAD_DIR, tool_data["stored_name"])
                    if os.path.exists(save_path):
                        file_to_send = discord.File(save_path, filename=tool_data["filename"])
                        await self.db.increment_download(tool_data["file_id"])

                history.append(response.candidates[0].content)
                history.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=fc.name,
                        response=tool_data,
                    )],
                ))

                response = _call_gemini(history)

            # 안전 필터 차단 체크
            if not response.candidates or not response.candidates[0].content.parts:
                logger.warning("Gemini 안전 필터에 의해 응답 차단됨")
                return "", None

            # 텍스트 parts에서 JSON이 아닌 실제 응답만 추출
            reply = ""
            for part in response.candidates[0].content.parts:
                if part.text and not part.text.strip().startswith("{"):
                    reply = part.text
                    break
            # 전부 JSON이면 마지막 텍스트라도 사용
            if not reply:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        reply = part.text
            history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))

            # 히스토리 20개 초과 시 오래된 것 제거
            if len(history) > 20:
                self.chat_histories[channel_id] = history[-20:]

            if len(reply) > 2000:
                reply = reply[:1997] + "..."

            return reply, file_to_send

        except ClientError as e:
            logger.error(f"Gemini ClientError: status={e.status} code={getattr(e, 'code', '?')} message={e}")
            if e.status == "RESOURCE_EXHAUSTED":
                # 일일 한도 vs 분당 한도 구분
                msg = str(e)
                if "PerDay" in msg or "per_day" in msg:
                    logger.warning("일일 한도 초과 (모든 키 소진)")
                    return self.persona.daily_limit_message, None
                else:
                    logger.warning("분당 한도 초과")
                    return self.persona.rate_limit_message, None
            return self.persona.error_message, None

        except Exception as e:
            logger.error(f"Gemini 에러: {type(e).__name__}: {e}")
            return self.persona.error_message, None
