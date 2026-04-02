"""
media_results에 file_hash 컬럼 추가 (SHA-256)
기존 레코드는 stored_name 기준으로 해시 계산
"""

import os
import json
import sqlite3
import hashlib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

data_dir = os.path.join(BASE_DIR, conf["paths"]["data_dir"])
db_path = os.path.join(data_dir, conf["db"]["librarian"])
media_dir = os.path.join(BASE_DIR, "librarian", "media")

if not os.path.exists(db_path):
    print(f"  DB 없음: {db_path}")
    exit(0)

conn = sqlite3.connect(db_path)

try:
    conn.execute("ALTER TABLE media_results ADD COLUMN file_hash TEXT")
    print("  media_results.file_hash 컬럼 추가 완료")
except sqlite3.OperationalError:
    print("  media_results.file_hash 컬럼 이미 존재")

conn.execute("CREATE INDEX IF NOT EXISTS idx_media_hash ON media_results(file_hash)")

# 기존 레코드 해시화
rows = conn.execute(
    "SELECT id, stored_name FROM media_results WHERE file_hash IS NULL AND stored_name IS NOT NULL").fetchall()
count = 0
for row in rows:
    path = os.path.join(media_dir, row[1])
    if os.path.exists(path):
        with open(path, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        conn.execute("UPDATE media_results SET file_hash = ? WHERE id = ?", (file_hash, row[0]))
        count += 1

conn.commit()
conn.close()
if count:
    print(f"  기존 미디어 해시화: {count}건")
