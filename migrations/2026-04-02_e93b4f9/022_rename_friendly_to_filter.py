"""
friendly → filter 리네임 (user_emotion 컬럼, emotion_log changes)
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)

# user_emotion: friendly → filter (ALTER TABLE RENAME COLUMN)
try:
    conn.execute("ALTER TABLE user_emotion RENAME COLUMN friendly TO filter")
except Exception as e:
    print(f"컬럼 리네임 실패 (이미 변경됨?): {e}")

# emotion_log: changes JSON 내 키 리네임
cursor = conn.execute("SELECT id, changes FROM emotion_log WHERE changes LIKE '%friendly%'")
for row in cursor.fetchall():
    new_changes = row[1].replace("friendly", "filter")
    conn.execute("UPDATE emotion_log SET changes = ? WHERE id = ?", (new_changes, row[0]))

conn.commit()
conn.close()
print("friendly → filter 리네임 완료")
