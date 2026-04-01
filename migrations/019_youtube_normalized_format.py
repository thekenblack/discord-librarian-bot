"""
url_results의 youtube normalized 형식 변경: youtube:ID → youtu.be/ID
"""

import os
import json
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

data_dir = os.path.join(BASE_DIR, conf["paths"]["data_dir"])
db_path = os.path.join(data_dir, conf["db"]["librarian"])

if not os.path.exists(db_path):
    print(f"  DB 없음: {db_path}")
    exit(0)

conn = sqlite3.connect(db_path)

cursor = conn.execute(
    "SELECT id, normalized FROM url_results WHERE normalized LIKE 'youtube:%'")
count = 0
for row in cursor.fetchall():
    old = row[1]
    video_id = old.removeprefix("youtube:")
    new = f"youtu.be/{video_id}"
    conn.execute("UPDATE url_results SET normalized = ? WHERE id = ?", (new, row[0]))
    count += 1

conn.commit()
conn.close()

if count:
    print(f"  youtube normalized 변환: {count}건 (youtube:ID → youtu.be/ID)")
else:
    print("  youtube normalized 변환 대상 없음")
