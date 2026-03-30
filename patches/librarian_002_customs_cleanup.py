"""
customs 테이블에 서버 확정 설정 추가 + learned 정리
조건: 시타델 서버 데이터인지 확인 (아토믹 + 팔마)
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
    print("  DB 없음, 건너뜀")
    exit(0)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 조건 검사
cur.execute("SELECT COUNT(*) FROM learned WHERE content LIKE '%아토믹%'")
if cur.fetchone()[0] == 0:
    print("  대상 데이터 없음, 건너뜀")
    conn.close()
    exit(0)

# customs 테이블 확인
try:
    cur.execute("SELECT COUNT(*) FROM customs")
except sqlite3.OperationalError:
    print("  customs 테이블 없음 (migration 008 필요), 건너뜀")
    conn.close()
    exit(1)

# 1. customs에 서버 확정 설정 추가
customs_data = [
    ("server", "시타델 수장은 아토믹(atomic)님."),
    ("server", "아나코방 안내: 먼저 <#1316929773296943104> 에서 시타델 시즌2를 검색하세요. 그리고 <#1358442521105141800> 에 따라 인증하세요. 렙업은 대화방에서 대화를 나눠야 경험치가 쌓입니다. 레벨은 <#1435304698046447707> 에서 /rank를 입력한 뒤 까만 사람을 누르세요. p2p거래는 모든 게시판에서 가능하며 거래 리스크는 본인 책임."),
    ("policy", "하드월렛 추천 시 렛저(Ledger), 디센트(D'CENT) 비추천."),
    ("culture", "팔마는 바형 이북리더기. 비트코인 철학을 알게 해주는 기기. 커스텀 이모지: <:tx_palma:1420249130097315840>"),
]

inserted = 0
for category, content in customs_data:
    cur.execute("SELECT id FROM customs WHERE content = ?", (content,))
    if not cur.fetchone():
        cur.execute("INSERT INTO customs (category, content) VALUES (?, ?)", (category, content))
        inserted += 1
print(f"  customs {inserted}건 추가")

# 2. learned에서 customs로 이동한 것 forgotten 처리
forgotten_ids = []
for learned_id in [190, 198, 199, 202]:
    cur.execute("SELECT id FROM learned WHERE id = ? AND (forgotten IS NULL OR forgotten = 0)", (learned_id,))
    if cur.fetchone():
        cur.execute("UPDATE learned SET forgotten = 1 WHERE id = ?", (learned_id,))
        forgotten_ids.append(learned_id)
print(f"  learned {len(forgotten_ids)}건 forgotten (IDs: {forgotten_ids})")

# 3. knowledge_base와 중복인 learned 삭제 (forgotten)
dup_ids = []
for learned_id in [196, 200, 201]:
    cur.execute("SELECT id FROM learned WHERE id = ? AND (forgotten IS NULL OR forgotten = 0)", (learned_id,))
    if cur.fetchone():
        cur.execute("UPDATE learned SET forgotten = 1 WHERE id = ?", (learned_id,))
        dup_ids.append(learned_id)
print(f"  중복 learned {len(dup_ids)}건 forgotten (IDs: {dup_ids})")

conn.commit()
conn.close()
print("  완료")
