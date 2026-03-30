"""
web_results, media_results 테이블 추가
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

conn.execute("""
    CREATE TABLE IF NOT EXISTS web_results (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        query      TEXT NOT NULL,
        result     TEXT NOT NULL,
        user_name  TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS media_results (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        filename   TEXT NOT NULL,
        result     TEXT NOT NULL,
        user_name  TEXT,
        uploader   TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
""")

conn.commit()
conn.close()
print("  web_results + media_results 테이블 생성 완료")
