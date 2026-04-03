import json
import logging
from google.genai import types
import importlib as _il
_tools = _il.import_module("librarian.layers.02_functioning.tools")
_eval_tools = _il.import_module("librarian.layers.05_evaluation.tools")
evaluation_tools = _eval_tools.evaluation_tools
execute_tool = _tools.execute_tool

logger = logging.getLogger("AILibrarian")


async def run_evaluation(self, user_id: str, user_name: str,
                         user_text: str, bot_reply: str,
                         context: str = "", tool_results: str = "",
                         channel_id: str = None):
    """Evaluation: 감정/기억/요약 업데이트. 백그라운드 실행, 에러 무시. API 1회 호출."""
    try:
        import re as _re

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

        # 이전 요약 조회
        prev_user_summary = await self.librarian_db.get_user_summary(user_id)
        prev_channel_summary = await self.librarian_db.get_channel_summary(channel_id) if channel_id else None

        # Evaluation 프롬프트
        sys_parts = []
        if self.persona.evaluation_text:
            sys_parts.append(self.persona.evaluation_text)
        sys_parts.append(emo_block)
        if prev_user_summary:
            sys_parts.append(f"## {user_name}과의 이전 대화 요약\n{prev_user_summary}")
        if prev_channel_summary:
            sys_parts.append(f"## 이 채널 이전 흐름 요약\n{prev_channel_summary}")
        if context:
            sys_parts.append(f"## 상황 분석 (Perception)\n{context}")
        if tool_results:
            sys_parts.append(f"## 도구 결과 (Functioning)\n{tool_results}")
        system_prompt = "\n\n".join(p for p in sys_parts if p)

        # 유저 메시지 + 봇 응답을 평가 대상으로 전달
        eval_text = f"유저({user_name}): {user_text}\n봇 응답: {bot_reply}"

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=evaluation_tools,
            max_output_tokens=1000,
            temperature=0.3,
        )

        # 단일 히스토리 + 이번 턴 (큐 워커가 직렬 실행하므로 락 불필요)
        loop_contents = list(self.evaluation_history)
        loop_contents.append(types.Content(role="user", parts=[types.Part.from_text(text=eval_text)]))

        logger.info(f"[Evaluation] API 호출 (히스토리={len(self.evaluation_history)}턴)")
        response = await self._call_gemini(loop_contents, config)

        # 1회 응답에서 모든 function_call + 텍스트 추출
        feedback_text = ""
        if response and response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                # 텍스트 (피드백/소견)
                if part.text and part.text.strip():
                    feedback_text = part.text.strip()

                # function_call 실행
                if not part.function_call:
                    continue
                fc = part.function_call
                logger.info(f"[Evaluation] 도구: {fc.name}({fc.args})")

                # feel
                if fc.name == "feel":
                    feel_args = dict(fc.args) if fc.args else {}
                    feel_msg_id = feel_args.pop("message_id", "")
                    reason = feel_args.pop("reason", "")
                    feel_args.pop("response", None)
                    feel_args.pop("reaction", None)

                    # 봇 전체 축
                    bot_changes = {}
                    for axis in self.librarian_db.SELF_AXES + self.librarian_db.SERVER_AXES:
                        if axis in feel_args:
                            try:
                                bot_changes[axis] = int(feel_args[axis])
                            except (ValueError, TypeError):
                                pass

                    # 유저별 처리
                    targets_raw = feel_args.get("targets") or []
                    if not targets_raw:
                        targets_raw = [{"user_id": user_id}]

                    for t in targets_raw:
                        t = dict(t) if t else {}
                        target_raw = t.get("user_id", user_id)
                        target_id = user_id
                        target_name = user_name
                        if target_raw:
                            id_match = _re.search(r'(\d{15,})', str(target_raw))
                            if id_match:
                                target_id = id_match.group(1)
                                target_name = str(target_raw)
                            else:
                                target_name = str(target_raw)
                                target_id = target_raw

                        changes = dict(bot_changes)
                        for axis in self.librarian_db.USER_AXES:
                            if axis in t:
                                try:
                                    changes[axis] = int(t[axis])
                                except (ValueError, TypeError):
                                    pass

                        current = await self.librarian_db.update_emotion(
                            changes, target_user_id=target_id,
                            target_user_name=target_name, reason=reason,
                            message_id=feel_msg_id or None)

                        if current:
                            changes_str = " ".join(f"{k}:{v:+d}" if isinstance(v, int) else f"{k}:{v:+.1f}" for k, v in changes.items())
                            current_str = " ".join(f"{k}:{v:.1f}" for k, v in current.items())
                            logger.info(f"[Evaluation] 감정: {target_name} | {changes_str} | {reason} → {current_str}")

                        bot_changes = {}  # 봇 축은 첫 유저에서만

                # memorize / forget
                elif fc.name in ("memorize", "forget"):
                    tool_args = dict(fc.args) if fc.args else {}
                    if fc.name == "memorize":
                        tool_args["_user_id"] = user_id
                        tool_args["_user_name"] = user_name
                    tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                    logger.info(f"[Evaluation] {fc.name}: {tool_result}")

                # update_summary
                elif fc.name == "update_summary":
                    summary = (dict(fc.args) if fc.args else {}).get("summary", "")
                    if summary:
                        await self.librarian_db.save_user_summary(user_id, summary)
                        logger.info(f"[Evaluation] 유저 요약 갱신 ({len(summary)}자): {summary[:100]}")

                # update_channel_summary
                elif fc.name == "update_channel_summary":
                    summary = (dict(fc.args) if fc.args else {}).get("summary", "")
                    if summary and channel_id:
                        await self.librarian_db.save_channel_summary(channel_id, summary)
                        logger.info(f"[Evaluation] 채널 요약 갱신 ({len(summary)}자): {summary[:100]}")

                # memorize_alias / forget_alias
                elif fc.name in ("memorize_alias", "forget_alias"):
                    tool_args = dict(fc.args) if fc.args else {}
                    tool_result = await execute_tool(self.library_db, self.librarian_db, fc.name, tool_args)
                    logger.info(f"[Evaluation] {fc.name}: {tool_result}")

                else:
                    logger.warning(f"[Evaluation] 알 수 없는 도구 무시: {fc.name}")

        # 피드백 저장
        if feedback_text:
            await self.librarian_db.save_feedback(user_id, feedback_text)
            logger.info(f"[Evaluation] 피드백 저장 ({len(feedback_text)}자): {feedback_text}")

        # L5 단일 히스토리에 이번 턴 추가
        self.evaluation_history.append(types.Content(role="user", parts=[
            types.Part.from_text(text=eval_text)]))
        self.evaluation_history.append(types.Content(role="model", parts=[
            types.Part.from_text(text=feedback_text if feedback_text else "(평가 완료)")]))
        self._trim_evaluation_history()

        logger.info("[Evaluation] 완료")

    except Exception as e:
        # Evaluation 에러는 무시 (응답에 영향 없음)
        logger.warning(f"[Evaluation] 에러 (무시): {type(e).__name__}: {e}")
