"""
Layer 05: Evaluation (커맨드 센터)
배치 단위 처리. 감정/요약/프로필/패턴/소견.
"""

import json
import logging
from google.genai import types
from config import TEMP_L5
import importlib as _il
_tools = _il.import_module("librarian.layers.02_execution.tools")
_eval_tools = _il.import_module("librarian.layers.05_evaluation.tools")
evaluation_tools = _eval_tools.evaluation_tools
execute_tool = _tools.execute_tool

logger = logging.getLogger("AILibrarian")


async def run_evaluation_batch(self, batch: list[dict]):
    """배치 처리. batch = [{"user_id", "user_name", "user_text", "bot_reply", "raw_reply", "context", "tool_results", "channel_id"}, ...]"""
    try:
        import re as _re

        # ── 배치에 등장하는 유저들의 DB 데이터 미리 로드 ──
        user_ids = {t["user_id"] for t in batch}
        profiles = {}
        emotions = {}
        summaries = {}
        for uid in user_ids:
            profiles[uid] = await self.librarian_db.get_user_profile(uid)
            emotions[uid] = await self.librarian_db.get_user_emotion(uid)
            uname = next((t["user_name"] for t in batch if t["user_id"] == uid), uid)
            summaries[uid] = await self.librarian_db.get_user_summary(uid)

        # 채널 요약
        channel_ids = {t["channel_id"] for t in batch if t.get("channel_id")}
        channel_summaries = {}
        for cid in channel_ids:
            channel_summaries[cid] = await self.librarian_db.get_channel_summary(cid)

        # 봇 감정
        bot_emo = await self.librarian_db.get_bot_emotion()

        # 패턴/자기 기록
        patterns = await self.librarian_db.get_pattern_notes(limit=10)
        self_notes = await self.librarian_db.get_self_notes(limit=5)

        # 서버 히스토리 (이전 L5 세션 요약)
        server_history = await self.librarian_db.get_recent_conversation_logs(limit=5)

        # ── 프롬프트 조립 ──
        sys_parts = []
        if self.persona.evaluation_text:
            sys_parts.append(self.persona.evaluation_text)

        # 봇 감정
        emo_lines = ["자체: " + " ".join(f"{k}:{v:.1f}" for k, v in bot_emo.items())]
        for uid in user_ids:
            emo = emotions.get(uid)
            uname = next((t["user_name"] for t in batch if t["user_id"] == uid), uid)
            if emo:
                emo_lines.append(f"@{uname}: " + " ".join(f"{k}:{emo[k]:.1f}" for k in self.librarian_db.USER_AXES) + f" (대화 {emo['interaction_count']}회)")
            else:
                emo_lines.append(f"@{uname}: 첫 방문")
        sys_parts.append("## 현재 감정 (50이 중립, 0-100)\n" + "\n".join(emo_lines))

        # 유저 프로필
        for uid in user_ids:
            prof = profiles.get(uid)
            uname = next((t["user_name"] for t in batch if t["user_id"] == uid), uid)
            if prof and any(prof.get(k) for k in ("personality", "trust_evidence", "preferences", "risk_notes", "relationship")):
                lines = [f"## @{uname} 프로필"]
                for k in ("personality", "trust_evidence", "preferences", "risk_notes", "relationship"):
                    if prof.get(k):
                        lines.append(f"  {k}: {prof[k]}")
                sys_parts.append("\n".join(lines))

        # 유저/채널 요약
        for uid in user_ids:
            uname = next((t["user_name"] for t in batch if t["user_id"] == uid), uid)
            s = summaries.get(uid)
            if s:
                sys_parts.append(f"## @{uname} 이전 요약\n{s}")
        for cid in channel_ids:
            s = channel_summaries.get(cid)
            if s:
                sys_parts.append(f"## 채널 이전 요약\n{s}")

        # 패턴/자기 기록
        if patterns:
            sys_parts.append("## 패턴 기록\n" + "\n".join(f"- [{p['scope']}] {p['observation']}" for p in patterns))
        if self_notes:
            sys_parts.append("## 자기 기록\n" + "\n".join(f"- [{n['category']}] {n['content']}" for n in self_notes))

        # 유저별 thinking 설정
        thinking_lines = []
        for uid in user_ids:
            uname = next((t["user_name"] for t in batch if t["user_id"] == uid), uid)
            ut = await self.librarian_db.get_user_thinking(uid)
            thinking_lines.append(f"@{uname}: L1={ut['l1']} L2={ut['l2']} L3={ut['l3']}")
        sys_parts.append("## 현재 thinking 설정\n" + "\n".join(thinking_lines))

        # 서버 히스토리
        if server_history:
            sys_parts.append("## 서버 히스토리 (이전 세션)\n" + "\n".join(
                f"- {h['created_at'][:16]}: {h['quality'][:100]}" for h in server_history))

        system_prompt = "\n\n".join(p for p in sys_parts if p)

        # ── 배치 대화쌍 구성 ──
        batch_lines = []
        for i, turn in enumerate(batch):
            uname = turn["user_name"]
            batch_lines.append(f"--- 턴 {i+1} ---")
            batch_lines.append(f"@{uname}: {turn['user_text']}")
            if i == 0 and turn.get("context"):
                batch_lines.append(f"[L1 분석] {turn['context'][:500]}")
            if turn.get("tool_results"):
                batch_lines.append(f"[L2 보고] {turn['tool_results'][:300]}")
            raw = turn.get("raw_reply", turn.get("bot_reply", ""))
            reply = turn.get("bot_reply", "")
            batch_lines.append(f"[L3 대사] {raw}")
            if raw != reply:
                batch_lines.append(f"[L4 최종] {reply}")
            if i == len(batch) - 1 and turn.get("context"):
                batch_lines.append(f"[L1 분석] {turn['context'][:500]}")

        eval_text = "\n".join(batch_lines)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=evaluation_tools,
            max_output_tokens=2000,
            temperature=TEMP_L5,
        )

        contents = [types.Content(role="user", parts=[types.Part.from_text(text=eval_text)])]

        from librarian.core import MODEL_L5
        logger.info(f"[Evaluation] 배치 API 호출 ({len(batch)}턴, model={MODEL_L5})")
        response = await self._call_gemini(contents, config, model=MODEL_L5)

        # ── 도구 결과 처리 ──
        feedback_text = ""
        if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.text:
                    feedback_text = part.text.strip()

                if not part.function_call:
                    continue
                fc = part.function_call
                fc_args = dict(fc.args) if fc.args else {}
                logger.info(f"[Evaluation] 도구: {fc.name}({fc_args})")

                if fc.name == "feel":
                    # 기존 feel 처리 (배치의 마지막 턴 기준)
                    last_turn = batch[-1]
                    targets = fc_args.get("targets") or []
                    reason = fc_args.get("reason", "")
                    feel_msg_id = fc_args.get("message_id")
                    bot_changes = {k: int(fc_args[k]) for k in ("self_mood", "self_energy", "server_vibe") if k in fc_args}

                    for t in (targets if targets else []):
                        target_id = str(t.get("user_id", last_turn["user_id"]))
                        target_name = t.get("user_name") or next((turn["user_name"] for turn in batch if turn["user_id"] == target_id), target_id)
                        changes = dict(bot_changes)
                        for axis in self.librarian_db.USER_AXES:
                            if axis in t:
                                try:
                                    changes[axis] = int(t[axis])
                                except (ValueError, TypeError):
                                    pass

                        before_emo = await self.librarian_db.get_user_emotion(target_id)
                        before_bot = await self.librarian_db.get_bot_emotion()

                        current = await self.librarian_db.update_emotion(
                            changes, target_user_id=target_id,
                            target_user_name=target_name, reason=reason,
                            message_id=feel_msg_id or None)

                        if current:
                            changes_str = " ".join(f"{k}:{v:+d}" if isinstance(v, int) else f"{k}:{v:+.1f}" for k, v in changes.items())
                            before_str = ""
                            if before_emo:
                                before_str = " ".join(f"{k}:{before_emo.get(k, 50):.1f}" for k in self.librarian_db.USER_AXES)
                            before_str += " " + " ".join(f"{k}:{before_bot.get(k, 50):.1f}" for k in self.librarian_db.SELF_AXES + self.librarian_db.SERVER_AXES)
                            current_str = " ".join(f"{k}:{v:.1f}" for k, v in current.items())
                            logger.info(f"[Evaluation] 감정: {target_name} | {changes_str} | {reason}\n  전: {before_str.strip()}\n  후: {current_str}")

                        bot_changes = {}

                elif fc.name == "memorize":
                    content = fc_args.get("content", "")
                    last_user = batch[-1].get("_user_name", "")
                    result = await execute_tool(self.library_db, self.librarian_db, "memorize",
                                                {"content": content, "_user_name": last_user})
                    logger.info(f"[Evaluation] memorize: {result}")

                elif fc.name == "forget":
                    result = await execute_tool(self.library_db, self.librarian_db, "forget", fc_args)
                    logger.info(f"[Evaluation] forget: {result}")

                elif fc.name == "memorize_alias":
                    result = await execute_tool(self.library_db, self.librarian_db, "memorize_alias", fc_args)
                    logger.info(f"[Evaluation] memorize_alias: {result}")

                elif fc.name == "forget_alias":
                    result = await execute_tool(self.library_db, self.librarian_db, "forget_alias", fc_args)
                    logger.info(f"[Evaluation] forget_alias: {result}")

                elif fc.name == "update_summary":
                    summary = fc_args.get("summary", "")
                    last_uid = batch[-1]["user_id"]
                    await self.librarian_db.save_user_summary(last_uid, summary)
                    logger.info(f"[Evaluation] 유저 요약 갱신 ({len(summary)}자): {summary[:100]}")

                elif fc.name == "update_channel_summary":
                    summary = fc_args.get("summary", "")
                    last_cid = batch[-1].get("channel_id")
                    if last_cid:
                        await self.librarian_db.save_channel_summary(last_cid, summary)
                        logger.info(f"[Evaluation] 채널 요약 갱신 ({len(summary)}자): {summary[:100]}")

                elif fc.name == "update_profile":
                    uid = fc_args.pop("user_id", batch[-1]["user_id"])
                    await self.librarian_db.upsert_user_profile(uid, **fc_args)
                    logger.info(f"[Evaluation] 프로필 갱신: {uid} → {fc_args}")

                elif fc.name == "log_conversation":
                    await self.librarian_db.save_conversation_log(
                        channel_id=fc_args.get("channel_id", batch[-1].get("channel_id", "")),
                        participants=fc_args.get("participants", ""),
                        quality=fc_args.get("quality", ""),
                        key_moments=fc_args.get("key_moments", ""),
                        layer_feedback=fc_args.get("layer_feedback", feedback_text[:500]))
                    logger.info(f"[Evaluation] 대화 로그: {fc_args.get('quality', '')[:100]}")

                elif fc.name == "note_pattern":
                    await self.librarian_db.save_pattern_note(
                        observation=fc_args.get("observation", ""),
                        scope=fc_args.get("scope", "global"),
                        target_id=fc_args.get("target_id"))
                    logger.info(f"[Evaluation] 패턴: {fc_args.get('observation', '')[:100]}")

                elif fc.name == "note_self":
                    await self.librarian_db.save_self_note(
                        content=fc_args.get("content", ""),
                        category=fc_args.get("category", "tendency"))
                    logger.info(f"[Evaluation] 자기 기록:\n{fc_args.get('content', '')}")

                elif fc.name == "feedback_user":
                    uid_raw = fc_args.get("user_id", batch[-1]["user_id"])
                    # L5가 닉네임(@Ken)을 넣을 수 있으므로 ID로 변환
                    import re as _re_uid
                    uid_match = _re_uid.search(r'(\d{15,})', str(uid_raw))
                    uid = uid_match.group(1) if uid_match else next(
                        (t["user_id"] for t in batch if t["user_name"] in str(uid_raw)), batch[-1]["user_id"])
                    fb = fc_args.get("feedback", "")
                    await self.librarian_db.save_feedback(uid, fb)
                    logger.info(f"[Evaluation] 유저 피드백 ({uid}):\n{fb}")

                elif fc.name == "feedback_channel":
                    cid = fc_args.get("channel_id", batch[-1].get("channel_id", ""))
                    fb = fc_args.get("feedback", "")
                    await self.librarian_db.save_channel_feedback(cid, fb)
                    logger.info(f"[Evaluation] 채널 피드백 ({cid}):\n{fb}")

                elif fc.name == "feedback_global":
                    fb = fc_args.get("feedback", "")
                    await self.librarian_db.save_global_feedback(fb)
                    logger.info(f"[Evaluation] 전체 피드백:\n{fb}")

                elif fc.name == "set_thinking":
                    uid = fc_args.get("user_id", batch[-1]["user_id"])
                    await self.librarian_db.set_user_thinking(
                        uid,
                        l1=fc_args.get("l1"),
                        l2=fc_args.get("l2"),
                        l3=fc_args.get("l3"))
                    parts = [f"{k}={fc_args[k]}" for k in ("l1", "l2", "l3") if k in fc_args]
                    logger.info(f"[Evaluation] thinking 조정: {uid} → {', '.join(parts)}")

        # ── 텍스트 피드백 (도구로 안 보낸 경우 폴백) ──
        if feedback_text:
            logger.info(f"[Evaluation] 텍스트 피드백:\n{feedback_text}")

        logger.info(f"[Evaluation] 배치 완료 ({len(batch)}턴)")

    except Exception as e:
        logger.warning(f"[Evaluation] 배치 처리 실패: {e}")
