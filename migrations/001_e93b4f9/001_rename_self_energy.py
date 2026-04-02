"""
self_energy → self_capacity 리네임 (bot_emotion 테이블 key, emotion_log changes)
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)

# bot_emotion 테이블: self_energy 값을 self_capacity로 이전 후 삭제
# (init()에서 self_capacity가 이미 생성될 수 있으므로 UPDATE 대신 값 복사)
row = conn.execute("SELECT value, updated_at FROM bot_emotion WHERE key = 'self_energy'").fetchone()
if row:
    conn.execute("UPDATE bot_emotion SET value = ?, updated_at = ? WHERE key = 'self_capacity'", (row[0], row[1]))
    conn.execute("DELETE FROM bot_emotion WHERE key = 'self_energy'")

# emotion_log: changes JSON 내 키 리네임
cursor = conn.execute("SELECT id, changes FROM emotion_log WHERE changes LIKE '%self_energy%'")
for row in cursor.fetchall():
    new_changes = row[1].replace("self_energy", "self_capacity")
    conn.execute("UPDATE emotion_log SET changes = ? WHERE id = ?", (new_changes, row[0]))

conn.commit()
conn.close()
print("self_energy → self_capacity 리네임 완료")
