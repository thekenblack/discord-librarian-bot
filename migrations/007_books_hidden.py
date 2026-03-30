"""
books 테이블에 hidden 컬럼 추가
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
try:
    conn.execute("ALTER TABLE books ADD COLUMN hidden INTEGER DEFAULT 0")
    print("  컬럼 추가: hidden")
except sqlite3.OperationalError:
    pass

conn.commit()
conn.close()
print("  완료")
