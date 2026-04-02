"""
url_results 테이블 생성 + web_results에서 URL 데이터 이전
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
conn.row_factory = sqlite3.Row

conn.execute("""
    CREATE TABLE IF NOT EXISTS url_results (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        normalized   TEXT NOT NULL,
        original_url TEXT NOT NULL,
        result       TEXT NOT NULL,
        user_name    TEXT,
        status       TEXT NOT NULL DEFAULT 'done',
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    )
""")
conn.execute("CREATE INDEX IF NOT EXISTS idx_url_normalized ON url_results(normalized)")

# web_results에서 URL 데이터 이전
try:
    rows = conn.execute(
        "SELECT query, result, user_name, original_url, status, created_at FROM web_results WHERE original_url IS NOT NULL").fetchall()
    count = 0
    for row in rows:
        if row["status"] != "done":
            continue
        conn.execute("""
            INSERT OR IGNORE INTO url_results (normalized, original_url, result, user_name, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (row["query"], row["original_url"], row["result"], row["user_name"], row["status"], row["created_at"]))
        count += 1
    conn.execute("DELETE FROM web_results WHERE original_url IS NOT NULL")
    if count:
        print(f"  URL 데이터 이전: {count}건")
except sqlite3.OperationalError as e:
    print(f"  URL 이전 건너뜀 (컬럼 없을 수 있음): {e}")

# web_results 정리 (status/original_url 컬럼 제거)
try:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS web_results_new (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            query      TEXT NOT NULL,
            result     TEXT NOT NULL,
            user_name  TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        INSERT INTO web_results_new (id, query, result, user_name, created_at)
        SELECT id, query, result, user_name, created_at FROM web_results
        WHERE original_url IS NULL OR original_url = ''
    """)
    conn.execute("DROP TABLE web_results")
    conn.execute("ALTER TABLE web_results_new RENAME TO web_results")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_query ON web_results(query)")
    print("  web_results 컬럼 정리 완료")
except Exception as e:
    print(f"  web_results 정리 건너뜀: {e}")

conn.commit()
conn.close()
print("  url_results 마이그레이션 완료")
