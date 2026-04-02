"""
filter 값 반전: 100 - value
friendly(높으면 친함)에서 filter(높으면 조심)로 방향이 바뀌었으므로 기존 값을 반전.
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)

# user_emotion: filter = 100 - filter
conn.execute("UPDATE user_emotion SET filter = 100 - filter")

# emotion_log: changes 내 filter 값 부호 반전은 하지 않음 (과거 기록은 당시 기준)

conn.commit()
conn.close()
print("filter 값 반전 완료 (100 - value)")
