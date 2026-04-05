"""
Layer 04: Postprocess (코드 기반)
멘션/채널/역할/이모지 변환. AI 호출 없음.
"""

import re
import logging

logger = logging.getLogger("AILibrarian")


async def run_postprocess(self, raw_reply: str, user_name: str,
                          mention_map: dict[str, str] | None = None,
                          channel_map: dict[str, str] | None = None,
                          role_map: dict[str, str] | None = None,
                          emoji_map: dict[str, str] | None = None,
                          feedback: str = "") -> str:
    """코드 기반 멘션/채널/역할/이모지 변환. AI 호출 없음."""
    if not raw_reply or not raw_reply.strip():
        return ""

    result = raw_reply

    # 멘션 변환: @닉네임 → <@ID> (퍼지 매칭)
    if mention_map:
        def _replace_mention(m):
            raw_name = m.group(1)
            # 정확한 매칭
            if raw_name in mention_map:
                return f"<@{mention_map[raw_name]}>"
            # 소문자 매칭
            lower_map = {k.lower(): v for k, v in mention_map.items()}
            if raw_name.lower() in lower_map:
                return f"<@{lower_map[raw_name.lower()]}>"
            # 부분 매칭 (닉네임이 포함된 경우: @켄님 → Ken)
            for name, uid in mention_map.items():
                if name.lower() in raw_name.lower() or raw_name.lower() in name.lower():
                    return f"<@{uid}>"
            return m.group()
        result = re.sub(r'@(\S+)', _replace_mention, result)

    # 채널 변환: #채널명 → <#ID>
    if channel_map:
        for name in sorted(channel_map.keys(), key=len, reverse=True):
            cid = channel_map[name]
            result = result.replace(f"#{name}", f"<#{cid}>")

    # 역할 변환: @역할명 → <@&ID>
    if role_map:
        for name in sorted(role_map.keys(), key=len, reverse=True):
            rid = role_map[name]
            result = result.replace(f"@{name}", f"<@&{rid}>")

    # 이모지 변환: :이름: → <:이름:ID>
    if emoji_map:
        for name, eid in emoji_map.items():
            result = result.replace(f":{name}:", eid)

    # 이미 변환된 멘션 중복 방지: <<@ID>> → <@ID>
    result = result.replace("<<@", "<@").replace(">>", ">")

    if result != raw_reply:
        logger.info(f"[Postprocess] 변환 완료")
    else:
        logger.info("[Postprocess] 변환 없음")

    return result
