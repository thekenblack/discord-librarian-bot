"""
web_results에 original_url 컬럼 추가
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

try:
    conn.execute("ALTER TABLE web_results ADD COLUMN original_url TEXT")
    print("  web_results.original_url 컬럼 추가 완료")
except sqlite3.OperationalError:
    print("  web_results.original_url 컬럼 이미 존재")

conn.commit()
conn.close()
