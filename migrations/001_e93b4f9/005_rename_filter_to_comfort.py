"""
filter → comfort 리네임 + 값 재반전 (filter는 높으면 조심, comfort는 높으면 편함)
024에서 friendly→filter 반전(100-value)을 했으므로, comfort로 바꾸면서 다시 반전.
결과적으로 원래 friendly 값으로 복귀.
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

db_path = os.path.join(BASE_DIR, conf["paths"]["data_dir"], conf["db"]["librarian"])

conn = sqlite3.connect(db_path)

# 컬럼 리네임
try:
    conn.execute("ALTER TABLE user_emotion RENAME COLUMN filter TO comfort")
except Exception as e:
    print(f"컬럼 리네임 실패 (이미 변경됨?): {e}")

# 값 재반전: filter(높으면 조심) → comfort(높으면 편함) = 100 - value
conn.execute("UPDATE user_emotion SET comfort = 100 - comfort")

# emotion_log: changes 키 리네임
cursor = conn.execute("SELECT id, changes FROM emotion_log WHERE changes LIKE '%filter%'")
for row in cursor.fetchall():
    new_changes = row[1].replace("filter", "comfort")
    conn.execute("UPDATE emotion_log SET changes = ? WHERE id = ?", (new_changes, row[0]))

conn.commit()
conn.close()
print("filter → comfort 리네임 + 값 재반전 완료")
