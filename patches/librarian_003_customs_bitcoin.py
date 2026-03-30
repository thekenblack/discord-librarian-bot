"""
customs에 비트코인 관련 상세 정보 추가
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

try:
    cur.execute("SELECT COUNT(*) FROM customs")
except sqlite3.OperationalError:
    print("  customs 테이블 없음, 건너뜀")
    conn.close()
    exit(1)

customs = [
    # 월렛 추천
    ("wallet", "하드월렛 추천: 키스톤(Keystone), 시드사이너(SeedSigner), 콜드카드(Coldcard), 패스포트(Passport)."),
    ("wallet", "소프트웨어 월렛: 스패로우(Sparrow)는 데스크톱 비트코인 전용 월렛. PSBT, 코인 컨트롤 지원."),
    ("wallet", "모바일 월렛: 그린(Green by Blockstream), 블루월렛(BlueWallet). 라이트닝 지원."),
    ("wallet", "셀프커스터디 원칙: 거래소에 두면 내 비트코인이 아님. Not your keys, not your coins."),
    ("wallet", "시드 구절(12/24단어)은 절대 디지털로 저장하면 안 됨. 금속 백업 권장."),
    ("wallet", "멀티시그: 여러 키로 서명해야 이동 가능. 대량 보관에 권장."),

    # 거래소/서비스
    ("exchange", "한국 거래소: 업비트, 빗썸 등. KYC 필수. 비트코인만 사고 바로 셀프커스터디로."),
    ("exchange", "P2P 거래: 비스큐(Bisq), 호들호들(HodlHodl). KYC 없는 탈중앙화 거래."),
    ("exchange", "DCA(적립식 매수): 매주/매월 일정 금액만큼 사는 전략. 시세 타이밍 걱정 없음."),

    # 비트코인 기술 심화
    ("tech", "UTXO: 비트코인 잔액 관리 방식. 쓰고 남은 거스름돈 개념. 프라이버시와 수수료에 영향."),
    ("tech", "PSBT(Partially Signed Bitcoin Transaction): 오프라인 서명용 표준. 에어갭 하드월렛에 필수."),
    ("tech", "코인조인(CoinJoin): 여러 사람의 트랜잭션을 합쳐서 프라이버시 강화."),
    ("tech", "OP_RETURN: 비트코인 블록체인에 데이터를 기록하는 방법. 80바이트 제한."),
    ("tech", "타임락(Timelock): 특정 시간/블록까지 비트코인을 잠그는 기능."),
    ("tech", "미니스크립트(Miniscript): 복잡한 비트코인 스크립트를 안전하게 작성하는 프레임워크."),

    # 비트코인 네트워크
    ("network", "풀노드 운영: 비트코인 네트워크에 직접 참여. 라즈베리파이로도 가능."),
    ("network", "Tor 연결: 풀노드를 Tor로 연결하면 IP 프라이버시 보호."),
    ("network", "노스트르(Nostr): 검열 저항 SNS 프로토콜. 라이트닝과 연동. 비트코이너들이 많이 사용."),

    # 한국 비트코인 문화
    ("culture_kr", "삣코인: 비트코인의 한국식 줄임말."),
    ("culture_kr", "김치 프리미엄: 한국 거래소와 해외 거래소 간 가격 차이."),
    ("culture_kr", "비트코인 전용주의: 알트코인을 거래하거나 추천하지 않는 원칙."),

    # 로그 분석 기반 추가
    ("person", "오태민: 비트코인을 표방하면서 알트코인(루나, 다닥 등)을 홍보하고 자체 토큰을 발행. 시타델에서는 사기꾼/교주로 간주."),
    ("server", "시타델 서버 관리자(수장)는 Ken(the_ken_black). 비트쨩의 주인이기도 함."),
    ("server", "아나코(Anarcho) 채널은 비트코인 P2P 거래 채널. KYC 없이 개인 간 거래."),
    ("server", "Citadel Guard는 시타델 서버의 레벨/경험치 관리 봇. 비트쨩과는 별개."),
    ("server", "비트쨩은 Ken이 만들었다. Google Gemini API로 구동되지만, 만든 사람은 Ken."),
    ("culture_kr", "호텔경제학: 한국의 비트코인/경제 유튜브 채널. 오스트리아 학파, 비트코인, 화폐의 역사를 다룸."),
    ("culture_kr", "네딸바: 대한민국의 대표적 비트코인 유튜버."),
    ("regulation", "비트코인은 검열 저항적이므로 개인 지갑 자체를 금지할 수 없다. 시드만 기억하면 어디서든 자산 접근 가능."),
    ("regulation", "비트코인 친화적 국가: 엘살바도르, 스위스, 포르투갈, UAE, 체코 등."),

    # 보안
    ("security", "DM으로 오는 투자 권유, 에어드롭, 지갑 연결 요청은 100% 사기."),
    ("security", "시드 구문을 디지털로 저장(스크린샷, 메모앱, 클라우드)하면 해킹에 노출. 금속 백업 권장."),
    ("security", "가짜 지갑 앱 주의. 공식 사이트에서만 다운로드. 앱스토어 리뷰만 믿으면 안 됨."),
]

inserted = 0
for category, content in customs:
    cur.execute("SELECT id FROM customs WHERE content = ?", (content,))
    if not cur.fetchone():
        cur.execute("INSERT INTO customs (category, content) VALUES (?, ?)", (category, content))
        inserted += 1

# 별칭 추가 (커뮤니티 약어)
aliases = [
    ("비트코인 스탠다드", "비스탠"),
    ("비트코인 디플로마", "비디"),
    ("비트코인 낙관론", "비낙"),
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
print(f"  customs {inserted}건 + 별칭 {alias_count}쌍 추가")
