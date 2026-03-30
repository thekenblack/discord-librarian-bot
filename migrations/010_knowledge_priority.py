"""
knowledge_base, customs에 priority 컬럼 추가
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
    exit(0)

conn = sqlite3.connect(db_path)

for table in ["knowledge_base", "customs"]:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN priority INTEGER DEFAULT 50")
        print(f"  {table}.priority 추가")
    except sqlite3.OperationalError:
        pass

# 카테고리별 기본 우선순위 설정
category_priority = {
    "bitcoin_basics": 95,
    "bip": 70,
    "money_history": 80,
    "bitcoin_culture": 75,
    "bitcoin_people": 75,
    "bitcoin_economics": 65,
    "bitcoin_tech_deep": 55,
    "bitcoin_technology": 60,
    "bitcoin_philosophy": 50,
    "bitcoin_history": 50,
    "bitcoin_lightning": 50,
    "bitcoin_books": 50,
    "ereader": 35,
    "austrian_economics": 65,
}

for cat, pri in category_priority.items():
    conn.execute("UPDATE knowledge_base SET priority = ? WHERE category = ?", (pri, cat))
    print(f"  knowledge_base/{cat}: priority = {pri}")

# customs 카테고리
customs_priority = {
    "server": 90,
    "policy": 80,
    "culture": 70,
    "wallet": 75,
    "exchange": 60,
    "tech": 55,
    "network": 50,
    "culture_kr": 65,
    "person": 70,
    "regulation": 60,
    "security": 80,
}

for cat, pri in customs_priority.items():
    conn.execute("UPDATE customs SET priority = ? WHERE category = ?", (pri, cat))
    print(f"  customs/{cat}: priority = {pri}")

conn.commit()
conn.close()
print("  완료")
