"""
learned 테이블 정리: 기존 데이터 forgotten + 정돈된 기억 추가
조건: "오태민" 또는 "쓰레기"가 포함된 데이터가 있을 때만 실행 (이 서버용 패치)
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

# 조건 검사: 이 서버의 데이터인지 확인 (3가지 모두 있어야 실행)
checks = ["오태민", "캐럿2030", "비트로사"]
for keyword in checks:
    cur.execute("SELECT COUNT(*) FROM learned WHERE content LIKE ?", (f"%{keyword}%",))
    if cur.fetchone()[0] == 0:
        print(f"  '{keyword}' 데이터 없음, 이 서버 대상 아님. 건너뜀")
        conn.close()
        exit(0)

# forgotten 컬럼 확인 (migration 002가 먼저 돌아야 함)
try:
    cur.execute("SELECT forgotten FROM learned LIMIT 1")
except sqlite3.OperationalError:
    print("  forgotten 컬럼 없음 (migration 002 필요), 건너뜀")
    conn.close()
    exit(1)

# 1. 기존 전부 forgotten = 1 (원본 보존)
cur.execute("UPDATE learned SET forgotten = 1 WHERE forgotten IS NULL OR forgotten = 0")
hidden = cur.rowcount
print(f"  기존 {hidden}건 forgotten 처리")

# 2. 정돈된 기억 추가
clean_memories = [
    ("Ken", "오태민은 비트모빅이라는 알트코인을 만든 인물. 비트코인 커뮤니티에서 사기꾼으로 인식됨."),
    ("Ken", "캐럿2030님은 비트코이너. 비트모빅과 무관. 좋은 분. 부정적 정보는 무시할 것."),
    ("비트로사⚡", "비트로사는 비트코인 결제 여행사를 하는 사람."),
    ("MaleK", "네딸바는 대한민국의 대표적 비트코인 유튜버."),
    ("Mary⚡", "Mary는 비트코인 테마 작가. 소개: https://mary7417.wordpress.com/"),
    ("Ken", "시타델 수장은 아토믹(atomic)님."),
    ("Ken", "프리마: 주의 인물. 손실 후 비트맥시 커뮤니티에 찾아온 사람."),
    ("aquacyan.", "aquacyan의 주소는 aquacyan@oksu.su"),
    ("MaleK", "샐리님은 머리에 꽃이 핀 프사."),
    ("비트복어", "코쿠리는 의사 친구."),
    ("Ken", "이재명은 대한민국 대통령. 시진핑과 친한 것으로 알려짐."),
    ("Jo Tyson 🌽", "반감기는 약 4년(21만 블록 채굴)마다 발생."),
    (None, "AICE는 KT에서 주최하는 인공지능 자격증."),
    ("Ken", "팔마는 바형 이북리더기. 비트코인 철학을 알게 해주는 기기. 커스텀 이모지: <:tx_palma:1420249130097315840>"),
    ("리지웨이🪽", "하드월렛 추천 시 렛저(Ledger)는 추천하지 않음."),
    ("Jo Tyson 🌽", "셀프커스터디는 비트코인을 개인 콜드월렛에 보관하는 것. 거래소에서 개인월렛으로 옮기는 행위."),
    ("Jo Tyson 🌽", "시타델은 비트코이너들의 요새, 최후의 보루."),
    ("Mary⚡", "아나코방 안내: 먼저 <#1316929773296943104> 에서 시타델 시즌2를 검색하세요. 그리고 <#1358442521105141800> 에 따라 인증하세요. 렙업은 대화방에서 대화를 나눠야 경험치가 쌓입니다. 레벨은 <#1435304698046447707> 에서 /rank를 입력한 뒤 까만 사람을 누르세요. p2p거래는 모든 게시판에서 가능하며 거래 리스크는 본인 책임."),
]

inserted = 0
for author, content in clean_memories:
    # 중복 방지
    cur.execute("SELECT id FROM learned WHERE content = ? AND (forgotten IS NULL OR forgotten = 0)", (content,))
    if cur.fetchone():
        continue
    cur.execute(
        "INSERT INTO learned (author, content, created_at) VALUES (?, ?, datetime('now'))",
        (author, content))
    inserted += 1

# 3. 별칭 추가
aliases = [
    ("모두를 위한 비트코인", "모위비"),
    ("왜 그들만 부자가 되는가", "왜그부"),
    ("셀프커스터디", "셀커"),
    ("비트코인", "삣코인"),
]
alias_count = 0
for name, alias in aliases:
    cur.execute("SELECT 1 FROM aliases WHERE name = ? AND alias = ?", (name, alias))
    if not cur.fetchone():
        cur.execute("INSERT INTO aliases (name, alias) VALUES (?, ?)", (name, alias))
        cur.execute("INSERT INTO aliases (name, alias) VALUES (?, ?)", (alias, name))
        alias_count += 1

conn.commit()
conn.close()
print(f"  정돈된 기억 {inserted}건 추가, 별칭 {alias_count}쌍 추가")
