"""
web_results 중 유튜브 URL의 query를 youtube:VIDEO_ID 형식으로 정규화
"""

import os
import json
import sqlite3
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

data_dir = os.path.join(BASE_DIR, conf["paths"]["data_dir"])
db_path = os.path.join(data_dir, conf["db"]["librarian"])

if not os.path.exists(db_path):
    print(f"  DB 없음: {db_path}")
    exit(0)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row


def _extract_youtube_id(url):
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


# original_url 컬럼 있는지 확인
try:
    rows = conn.execute(
        "SELECT id, query, original_url FROM web_results WHERE original_url IS NOT NULL").fetchall()
except sqlite3.OperationalError:
    print("  original_url 컬럼 없음, 건너뜀")
    conn.close()
    exit(0)

count = 0
for row in rows:
    content_id = _extract_youtube_id(row["original_url"])
    if not content_id:
        continue
    new_query = f"youtube:{content_id}"
    if row["query"] == new_query:
        continue
    existing = conn.execute(
        "SELECT id FROM web_results WHERE query = ? AND id != ?",
        (new_query, row["id"])).fetchone()
    if existing:
        conn.execute("DELETE FROM web_results WHERE id = ?", (row["id"],))
    else:
        conn.execute("UPDATE web_results SET query = ? WHERE id = ?", (new_query, row["id"]))
    count += 1

conn.commit()
conn.close()
if count:
    print(f"  유튜브 query 정규화: {count}건")
else:
    print("  정규화 대상 없음")
