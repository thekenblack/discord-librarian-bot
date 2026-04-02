"""
감정 범위 0~10 → 0~100 변환. 기존 값 x10.
기본값 5 → 50.
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)
c = conn.cursor()

# user_emotion: 유저별 감정 값 x10 (컬럼명이 리네임될 수 있으므로 동적으로)
cols = [r[1] for r in c.execute("PRAGMA table_info(user_emotion)").fetchall()]
real_cols = [c for c in cols if c not in ("user_id", "user_name", "interaction_count", "last_interaction")]
if real_cols:
    set_clause = ", ".join(f"{col} = {col} * 10" for col in real_cols)
    c.execute(f"UPDATE user_emotion SET {set_clause}")
print(f"user_emotion 변환: {c.rowcount}건")

# user_emotion 기본값 변경
c.execute("PRAGMA table_info(user_emotion)")
# SQLite는 ALTER TABLE로 DEFAULT를 변경할 수 없으므로, 신규 유저는 코드에서 처리

# bot_emotion: value x10
c.execute("UPDATE bot_emotion SET value = value * 10")
print(f"bot_emotion 변환: {c.rowcount}건")

conn.commit()
conn.close()
print("감정 범위 0~100 변환 완료")
