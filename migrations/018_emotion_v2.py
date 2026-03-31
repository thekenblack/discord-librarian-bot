"""
감정 시스템 v2: user_emotion 재설계 + bot_emotion 추가
기존 user_emotion (6축) → 새 user_emotion (3축) + bot_emotion (3축)
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

# 기존 user_emotion 백업 후 재생성
try:
    conn.execute("ALTER TABLE user_emotion RENAME TO user_emotion_old")
    print("  기존 user_emotion → user_emotion_old")
except sqlite3.OperationalError:
    pass

conn.execute("""
    CREATE TABLE IF NOT EXISTS user_emotion (
        user_id    TEXT PRIMARY KEY,
        user_name  TEXT,
        friendly   REAL NOT NULL DEFAULT 0,
        lovely     REAL NOT NULL DEFAULT 0,
        trust      REAL NOT NULL DEFAULT 0,
        interaction_count INTEGER NOT NULL DEFAULT 0,
        last_interaction TEXT
    )
""")

# 기존 데이터에서 이관 가능한 것 이관
try:
    rows = conn.execute("SELECT user_id, user_name, interaction_count, last_interaction FROM user_emotion_old").fetchall()
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO user_emotion (user_id, user_name, interaction_count, last_interaction) VALUES (?, ?, ?, ?)",
            row)
    print(f"  유저 감정 이관: {len(rows)}건")
except sqlite3.OperationalError:
    pass

# bot_emotion 테이블
conn.execute("""
    CREATE TABLE IF NOT EXISTS bot_emotion (
        key   TEXT PRIMARY KEY,
        value REAL NOT NULL DEFAULT 0,
        updated_at TEXT
    )
""")
for key in ("self_mood", "self_energy", "server_vibe"):
    conn.execute("INSERT OR IGNORE INTO bot_emotion (key, value) VALUES (?, 0)", (key,))

# emotion_log에 target 컬럼 추가 (기존 user_id → target)
try:
    conn.execute("ALTER TABLE emotion_log ADD COLUMN target TEXT")
    conn.execute("UPDATE emotion_log SET target = user_id WHERE target IS NULL")
    print("  emotion_log.target 컬럼 추가")
except sqlite3.OperationalError:
    print("  emotion_log.target 이미 존재")

conn.commit()
conn.close()
print("  감정 시스템 v2 마이그레이션 완료")
