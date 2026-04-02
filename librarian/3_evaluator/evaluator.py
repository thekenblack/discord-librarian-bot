import json
import logging
from google.genai import types
import importlib as _il
_tools = _il.import_module("librarian.1_processor.tools")
_eval_tools = _il.import_module("librarian.3_evaluator.tools")
evaluator_tools = _eval_tools.evaluator_tools
execute_tool = _tools.execute_tool

logger = logging.getLogger("AILibrarian")


async def run_evaluator(self, user_id: str, user_name: str,
                         user_text: str, bot_reply: str):
    """Evaluator: 감정/기억 업데이트. 백그라운드 실행, 에러 무시."""
    try:
        # 현재 감정 상태 조회
        bot_emo = await self.librarian_db.get_bot_emotion()
        user_emo = await self.librarian_db.get_user_emotion(user_id)

        emo_lines = []
        emo_lines.append("자체: " + " ".join(f"{k}:{v:.1f}" for k, v in bot_emo.items()))
        if user_emo:
            emo_lines.append(f"{user_name}: " + " ".join(f"{k}:{user_emo[k]:.1f}" for k in self.librarian_db.USER_AXES) + f" (대화 {user_emo['interaction_count']}회)")
        else:
            emo_lines.append(f"{user_name}: 첫 방문")
        emo_block = "## 현재 감정 (50이 중립, 0 ~ 100)\n" + "\n".join(emo_lines)

        # Evaluator 프롬프트
        sys_parts = []
        if self.persona.evaluator_text:
            sys_parts.append(self.persona.evaluator_text)
        sys_parts.append(emo_block)
        system_prompt = "\n\n".join(p for p in sys_parts if p)

        # 유저 메시지 + 봇 응답을 평가 대상으로 전달
        eval_text = f"유저({user_name}): {user_text}\n봇 응답: {bot_reply}"

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=evaluator_tools,
            max_output_tokens=500,
            temperature=0.5,
        )

        loop_contents = [types.Content(role="user", parts=[types.Part.from_text(text=eval_text)])]

        logger.info(f"[Evaluator] API 호출")
        response = await self._call_gemini(loop_contents, config)

        # 도구 루프 (최대 5회 — feel, memorize, forget)
        _feel_done = False
        for loop_i in range(5):
            if not response.candidates or not response.candidates[0].content.parts:
                break

            fc = None
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    fc = part.function_call
                    break
            if not fc:
                break

            logger.info(f"[Evaluator] 루프 {loop_i+1}: 도구 호출 {fc.name}({fc.args})")

            # feel 도구
            if fc.name == "feel":
                if _feel_done:
                    # 1회 제한
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

                target_id = user_id
                target_name = user_name
                if target_raw:
                    import re as _re
                    id_match = _re.search(r'(\d{15,})', str(target_raw))
                    if id_match:
                        target_id = id_match.group(1)
                        target_name = target_raw
                    else:
                        target_name = str(target_raw)
                        target_id = target_raw

                changes = {}
                for axis in self.librarian_db.ALL_AXES:
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
                logger.info(f"[Evaluator] 감정: {target_name} | {changes_str} | {reason} → {current_str}")
                _feel_done = True

                # reaction 로그만 남김 (실제 리액션은 on_message에서 처리)
                if reaction_emoji:
                    logger.info(f"[Evaluator] 리액션 예약 (무시됨, 이미 응답 전송 후): {reaction_emoji}")

                result_parts = []
                for k, v in current.items():
                    result_parts.append(f"{k} {v:.1f} (0 ~ 100)")
                result_str = " | ".join(result_parts)
                tool_data = {"result": result_str}

                loop_contents.append(response.candidates[0].content)
                loop_contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name="feel", response=tool_data)],
                ))
                try:
                    response = await self._call_gemini(loop_contents, config)
                except Exception as e:
                    logger.warning(f"[Evaluator] feel 후 API 에러: {e}")
                    break
                continue

            # memorize / forget 도구
            if fc.name in ("memorize", "forget"):
                tool_args = dict(fc.args) if fc.args else {}
                if fc.name == "memorize":
                    tool_args["_user_id"] = user_id
                    tool_args["_user_name"] = user_name
                tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                tool_data = json.loads(tool_result)
                logger.info(f"[Evaluator] {fc.name} 결과: {tool_result}")

                loop_contents.append(response.candidates[0].content)
                loop_contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(name=fc.name, response=tool_data)],
                ))
                try:
                    response = await self._call_gemini(loop_contents, config)
                except Exception as e:
                    logger.warning(f"[Evaluator] {fc.name} 후 API 에러: {e}")
                    break
                continue

            # 알 수 없는 도구 → 무시
            logger.warning(f"[Evaluator] 알 수 없는 도구 무시: {fc.name}")
            loop_contents.append(response.candidates[0].content)
            loop_contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(name=fc.name, response={"result": "unknown tool"})],
            ))
            try:
                response = await self._call_gemini(loop_contents, config)
            except Exception:
                break

        logger.info("[Evaluator] 완료")

    except Exception as e:
        # Evaluator 에러는 무시 (응답에 영향 없음)
        logger.warning(f"[Evaluator] 에러 (무시): {type(e).__name__}: {e}")
