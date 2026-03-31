"""
web_results 중 유튜브 URL인 것의 query를 youtube:VIDEO_ID 형식으로 정규화
original_url 기준으로 영상 ID 추출 후 query 업데이트
"""

import re
import aiosqlite
from urllib.parse import urlparse, parse_qs
from config import LIBRARIAN_DB_PATH


def _extract_youtube_id(url: str):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")
        if host == "youtu.be":
            return parsed.path.lstrip("/").split("/")[0] or None
        if host == "youtube.com":
            path = parsed.path.rstrip("/")
            if path.startswith("/watch"):
                return parse_qs(parsed.query).get("v", [None])[0]
            if path.startswith(("/shorts/", "/live/")):
                parts = path.split("/")
                return parts[2] if len(parts) > 2 else None
    except Exception:
        pass
    return None


async def run():
    async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, query, original_url FROM web_results WHERE original_url IS NOT NULL")
        rows = await cursor.fetchall()

        count = 0
        for row in rows:
            url = row["original_url"]
            content_id = _extract_youtube_id(url)
            if not content_id:
                continue
            new_query = f"youtube:{content_id}"
            if row["query"] == new_query:
                continue
            # 이미 같은 youtube: 키가 있으면 중복 방지
            existing = await db.execute(
                "SELECT id FROM web_results WHERE query = ? AND id != ?",
                (new_query, row["id"]))
            if await existing.fetchone():
                # 중복이면 기존 것 삭제
                await db.execute("DELETE FROM web_results WHERE id = ?", (row["id"],))
            else:
                await db.execute(
                    "UPDATE web_results SET query = ? WHERE id = ?",
                    (new_query, row["id"]))
            count += 1

        await db.commit()
        if count:
            print(f"유튜브 query 정규화: {count}건")
