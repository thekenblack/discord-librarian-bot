"""
customs, book_knowledge 테이블 추가
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
    CREATE TABLE IF NOT EXISTS customs (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        content  TEXT NOT NULL,
        alias    TEXT
    )
""")

conn.execute("""
    CREATE TABLE IF NOT EXISTS book_knowledge (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id  INTEGER,
        content  TEXT NOT NULL,
        source   TEXT
    )
""")

conn.commit()
conn.close()
print("  customs + book_knowledge 테이블 생성 완료")
