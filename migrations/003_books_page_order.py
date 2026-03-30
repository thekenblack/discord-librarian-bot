"""
pages 테이블 생성 + books에 page_id, sort_order 컬럼 추가
"""

import os
import json
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

data_dir = os.path.join(BASE_DIR, conf["paths"]["data_dir"])
db_path = os.path.join(data_dir, conf["db"]["library"])

if not os.path.exists(db_path):
    print(f"  DB 없음: {db_path}")
    exit(0)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# pages 테이블
cur.execute("""
    CREATE TABLE IF NOT EXISTS pages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
""")

# books에 page_id, sort_order 추가
for col, coltype in [("page_id", "INTEGER DEFAULT 0"), ("sort_order", "INTEGER DEFAULT 0")]:
    try:
        cur.execute(f"ALTER TABLE books ADD COLUMN {col} {coltype}")
        print(f"  컬럼 추가: {col}")
    except sqlite3.OperationalError:
        pass

# 기존 page 컬럼이 있으면 page_id로 마이그레이션
try:
    cur.execute("UPDATE books SET page_id = page WHERE page IS NOT NULL AND page > 0")
except Exception:
    pass

conn.commit()
conn.close()
print("  완료")
