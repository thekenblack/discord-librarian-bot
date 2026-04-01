"""
self_energy → self_capacity 리네임 (bot_emotion 테이블 key, emotion_log changes)
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json")) as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)

# bot_emotion 테이블: key 리네임
conn.execute("UPDATE bot_emotion SET key = 'self_capacity' WHERE key = 'self_energy'")

# emotion_log: changes JSON 내 키 리네임
cursor = conn.execute("SELECT id, changes FROM emotion_log WHERE changes LIKE '%self_energy%'")
for row in cursor.fetchall():
    new_changes = row[1].replace("self_energy", "self_capacity")
    conn.execute("UPDATE emotion_log SET changes = ? WHERE id = ?", (new_changes, row[0]))

conn.commit()
conn.close()
print("self_energy → self_capacity 리네임 완료")
