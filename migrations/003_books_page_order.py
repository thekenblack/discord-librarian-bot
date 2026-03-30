"""
books 테이블에 page, sort_order 컬럼 추가
- page: 페이지 번호 (0 = 미지정, 뒤로 밀림)
- sort_order: 페이지 내 순서 (0 = 미지정, 뒤로 밀림)
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

for col, coltype in [("page", "INTEGER DEFAULT 0"), ("sort_order", "INTEGER DEFAULT 0")]:
    try:
        cur.execute(f"ALTER TABLE books ADD COLUMN {col} {coltype}")
        print(f"  컬럼 추가: {col}")
    except sqlite3.OperationalError:
        pass

conn.commit()
conn.close()
print("  완료")
