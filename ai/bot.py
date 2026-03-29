"""
AI 사서봇 - Gemini function calling으로 도서관 기능 + 잡담
"""

import os
import json
import discord
import logging
from google import genai
from google.genai import types
from google.genai.errors import ClientError

from database import Database
from config import UPLOAD_DIR
from ai.persona import Persona
from ai.tools import library_tools, execute_tool

logger = logging.getLogger("AILibrarian")

MODEL = "gemini-2.5-flash"


class AILibrarianBot(discord.Client):
    def __init__(self, persona: Persona, gemini_api_key: str):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.persona = persona
        self.db = Database()
        self.gemini = genai.Client(api_key=gemini_api_key)
        self.chat_histories: dict[int, list] = {}  # channel_id -> history

    async def on_ready(self):
        await self.db.init()
        logger.info(f"{self.user} 온라인! ({self.persona.name})")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=self.persona.status_text
        ))

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not self.user or self.user not in message.mentions:
            return

        # 멘션 제거해서 실제 메시지 추출
        text = message.content
        for mention in [f"<@{self.user.id}>", f"<@!{self.user.id}>"]:
            text = text.replace(mention, "")
        text = text.strip()

        if not text:
            text = "안녕"

        async with message.channel.typing():
            reply_text, file_to_send = await self._ask_gemini(message.channel.id, text)

        if file_to_send:
            await message.reply(reply_text, file=file_to_send)
        else:
            await message.reply(reply_text)

    async def _ask_gemini(self, channel_id: int, user_text: str) -> tuple[str, discord.File | None]:
        """Gemini에게 질문하고 응답 + 파일(있으면) 반환"""
        if channel_id not in self.chat_histories:
            self.chat_histories[channel_id] = []
        history = self.chat_histories[channel_id]

        history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_text)]))

        file_to_send = None

        try:
            response = self.gemini.models.generate_content(
                model=MODEL,
                contents=history,
                config=types.GenerateContentConfig(
                    system_instruction=self.persona.system_prompt,
                    tools=library_tools,
                    max_output_tokens=500,
                    temperature=0.8,
                ),
            )

            # function call 루프 (최대 3회)
            for _ in range(3):
                # parts 중 function_call이 있는지 탐색
                fc = None
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        fc = part.function_call
                        break
                if not fc:
                    break

                logger.info(f"도구 호출: {fc.name}({fc.args})")

                tool_result = await execute_tool(self.db, fc.name, dict(fc.args) if fc.args else {})
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

                response = self.gemini.models.generate_content(
                    model=MODEL,
                    contents=history,
                    config=types.GenerateContentConfig(
                        system_instruction=self.persona.system_prompt,
                        tools=library_tools,
                        max_output_tokens=500,
                        temperature=0.8,
                    ),
                )

            reply = ""
            for part in response.candidates[0].content.parts:
                if part.text:
                    reply = part.text
                    break
            history.append(types.Content(role="model", parts=[types.Part.from_text(text=reply)]))

            if len(history) > 20:
                self.chat_histories[channel_id] = history[-20:]

            if len(reply) > 2000:
                reply = reply[:1997] + "..."

            return reply, file_to_send

        except ClientError as e:
            if e.status == "RESOURCE_EXHAUSTED" or e.code == 429:
                logger.warning("Gemini rate limit 초과")
                return self.persona.rate_limit_message, None
            logger.error(f"Gemini 클라이언트 에러: {e}")
            return self.persona.error_message, None

        except Exception as e:
            logger.error(f"Gemini 에러: {e}")
            return self.persona.error_message, None
