"""
감정 범위 0~10 → 0~100 변환. 기존 값 x10.
기본값 5 → 50.
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json")) as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)
c = conn.cursor()

# user_emotion: friendly, lovely, trust 값 x10
c.execute("UPDATE user_emotion SET friendly = friendly * 10, lovely = lovely * 10, trust = trust * 10")
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
