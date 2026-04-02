"""
learned 테이블 스키마 개선
- author 컬럼 추가 (발화자)
- reply_to 컬럼 추가 (답글 원본)
- forgotten 컬럼 추가 (soft delete)
- 기존 content에서 author, reply_to 파싱
"""

import os
import re
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
cur = conn.cursor()

# 컬럼 추가 (이미 있으면 무시)
for col, coltype in [("author", "TEXT"), ("reply_to", "TEXT"), ("forgotten", "INTEGER DEFAULT 0")]:
    try:
        cur.execute(f"ALTER TABLE learned ADD COLUMN {col} {coltype}")
        print(f"  컬럼 추가: {col}")
    except sqlite3.OperationalError:
        pass

# 기존 데이터 파싱
cur.execute("SELECT id, content FROM learned WHERE author IS NULL")
rows = cur.fetchall()
print(f"  파싱 대상: {len(rows)}건")

# 패턴: "이름: [원본: ...] 내용" 또는 "이름: 내용" 또는 그냥 "내용"
reply_pattern = re.compile(r'^\[원본:\s*(.+?)\]\s*(.*)', re.DOTALL)

for row_id, content in rows:
    author = None
    reply_to = None
    body = content

    # "이름: 내용" 패턴
    colon_idx = content.find(": ")
    if colon_idx > 0 and colon_idx < 50:
        candidate_author = content[:colon_idx]
        # 이름에 줄바꿈이나 특수 패턴이 없으면 이름으로 간주
        if "\n" not in candidate_author and "[" not in candidate_author:
            author = candidate_author
            body = content[colon_idx + 2:]

    # 답글 패턴: [원본: xxx] 실제내용
    reply_match = reply_pattern.match(body)
    if reply_match:
        reply_to = reply_match.group(1).strip()
        body = reply_match.group(2).strip()

    # 본문이 비어있으면 원본 유지
    if not body.strip():
        body = content
        author = None
        reply_to = None

    cur.execute(
        "UPDATE learned SET author = ?, reply_to = ?, content = ? WHERE id = ?",
        (author, reply_to, body.strip(), row_id),
    )

conn.commit()
conn.close()
print(f"  완료: {len(rows)}건 파싱")
