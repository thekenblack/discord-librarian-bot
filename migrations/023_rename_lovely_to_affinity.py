"""
lovely → affinity 리네임 (user_emotion 컬럼, emotion_log changes)
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json")) as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)

try:
    conn.execute("ALTER TABLE user_emotion RENAME COLUMN lovely TO affinity")
except Exception as e:
    print(f"컬럼 리네임 실패 (이미 변경됨?): {e}")

cursor = conn.execute("SELECT id, changes FROM emotion_log WHERE changes LIKE '%lovely%'")
for row in cursor.fetchall():
    new_changes = row[1].replace("lovely", "affinity")
    conn.execute("UPDATE emotion_log SET changes = ? WHERE id = ?", (new_changes, row[0]))

conn.commit()
conn.close()
print("lovely → affinity 리네임 완료")
