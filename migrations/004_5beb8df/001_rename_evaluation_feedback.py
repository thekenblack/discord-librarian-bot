"""
evaluator_feedback → evaluation_feedback 테이블 리네임
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)

# evaluator_feedback → evaluation_feedback 리네임
try:
    conn.execute("ALTER TABLE evaluator_feedback RENAME TO evaluation_feedback")
    print("evaluator_feedback → evaluation_feedback 리네임 완료")
except Exception as e:
    # 이미 리네임된 경우
    print(f"리네임 건너뜀: {e}")

conn.commit()
conn.close()
