"""
media_results에 stored_name 컬럼 추가 (로컬 저장 파일명)
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

# stored_name 컬럼 추가 (이미 있으면 무시)
try:
    conn.execute("ALTER TABLE media_results ADD COLUMN stored_name TEXT")
    print("  media_results.stored_name 컬럼 추가 완료")
except sqlite3.OperationalError:
    print("  media_results.stored_name 컬럼 이미 존재")

conn.commit()
conn.close()
