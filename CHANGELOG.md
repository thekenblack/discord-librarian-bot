# Changelog

배포 당시 의도를 기록한다. 배포 후 실제 동작은 FEEDBACKS.md에 기록.

---

## v7 (2026-04-05 ~)

v6 기반. 구조 재정의. 기능 추가 없음, 각 레이어의 역할과 정보 흐름을 명확화.

### 의도

레이어 역할 재정의:
- L1: 관찰 + 의도 파악 + 로컬 검색(search만). 비싼 호출 분리
- L2: Execution으로 개명. 로컬 데이터 보충 판단 + 모든 비싼 실행 (recognize, web_search, deliver, gift)
- L3: 순수 연기자. 공통 컨텍스트 원본 안 봄. L1/L2 해석만 받음
- L4: 멘션 퍼지 매칭 + 행동 정합성 검증 (L2 보고로 L3 대사 교차 검증)

공통 컨텍스트:
- DB 1회 조회 (shared_ctx). 감정, 요약, 잔고, 카탈로그, 기억 전부 포함
- L1/L2: 원본 직접 참조. L3: 원본 안 봄

@닉네임 통일:
- 맥락 포맷 이름(<@ID>) -> @이름. 전 레이어 동일
- L3가 @닉네임으로 출력, L4가 mention_map 기반 <@ID> 변환
- 히스토리에 raw_reply(변환 전) 저장. L3가 깨끗한 히스토리를 봄

프로세스 통합:
- 라이브러리 봇 + AI 사서봇을 main.py에서 하나의 asyncio 루프로 실행
- 상호 직접 호출 가능 (DB 폴링/마커 불필요)
- 봇->유저 선물 알림을 라이브러리 봇이 직접 전송 (L2 직후 즉시)

에러 수정:
- update_bot_emotion -> update_emotion (시급 루프 fullness/hydration 복구)
- NEEDS_AXES 필터 누락 (db.py)
- Perception 빈 응답 로깅
- Unknown Emoji 리액션 방어
- evaluation_worker task_done 중복 방어
- URL HTML MIME 사전 체크
- gift_user 잔고 체크 + gift_failed 보고
- L2 불필요 attach(media_id:0) 방지
- L3 전달 미완료 대사 방지 ([전달 성공] 엄격 규칙)

### 주요 변경

- main.py 추가 (통합 엔트리포인트)
- config.json: bots를 통합 봇 1개로, model_l2/model_l4 추가, version 7
- core.py: shared_ctx, _mention_map, _call_gemini model 파라미터, L2 직후 선물 처리, raw_reply 히스토리
- perception.py: gather_context가 shared_ctx 사용, 카탈로그/기억/경제 공통 포함, recognize 제거
- functioning.py: recognize_media/link 추가, 경제 블록 제거 (공통), shared_ctx 사용
- postprocess.py: instruction(L2 보고) 받아 행동 정합성 검증
- tools.py: recognize_media/link 선언 추가, gift_user 잔고 체크
- db.py: NEEDS_AXES 필터 추가
- L1 01_role.txt: 의도 파악 + 지식 판단 + 도구 현황 보고
- L2 01_role.txt: Execution + 로컬 데이터 보충 판단
- L2 05_instruction.txt: 판단 보충 + 도구 현황 보고
- L3 03_behavior.txt: @닉네임, 유니코드 수식, 디스코드 마크다운, 전달 규칙 엄격화
- L4 01_role.txt: 멘션 변환 + 행동 정합성 검증
- admin.py: /admin log server_log 옵션

---

## v6 (2026-04-04 ~)

v5 기반. 경제 시스템, 자발적 발화, 레이어 역할 재조정.

### 의도

경제 시스템:
- Lightning(Blink API) 기반 충전/구매 시스템
- /charge: Lightning 인보이스로 잔고 충전 (QR + 입금확인 + 폴링)
- /buy: 사서봇에게 선물. 잔고 부족 시 부족분만큼 인보이스 자동 발행, 입금 확인 후 선물 자동 전달
- /balance: 잔고 확인
- /admin charge: 어드민 직접 충전 (모달, user 생략 시 본인)
- 아이템 16종 (50-2100 sat), 가격순 정렬
- DB: wallets, transactions, invoices 테이블 (library.db)

자발적 발화:
- SPONTANEOUS_CHANNEL_ID 지정 시 랜덤 간격(30분-2시간)으로 자발적 발화
- 해당 채널에서 멘션 없이도 4초 debounce 후 응답 가능 (답글 아닌 일반 메시지로)

레이어 재조정:
- L1: web_search 제거. 로컬 검색(search, recognize_media, recognize_link)만
- L2: web_search 추가. L1 검색 결과 불충분 시만 사용. gift_user 도구 추가 (봇이 유저에게 선물)
- L4: 역할 축소. 원문 보존 우선, 시스템 흔적 제거와 디스코드 포맷 교정만

응답 모드:
- "무응답" 모드 제거
- no_comment: L3/L4 스킵, L5 실행. "듣고 있지만 끼어들지 않겠다"
- 리액션만: 기존과 동일

### 주요 변경

- library/lightning.py 추가 (Blink API 래퍼, Mock 모드)
- library/cogs/shop.py 전면 재작성 (경제 시스템)
- library/db.py: wallets, transactions, invoices 테이블 + 관련 메서드
- library/bot.py: LightningManager 초기화
- library/utils.py: sat_fmt, make_qr_file, LIGHTNING_COLOR
- library/cogs/admin.py: /admin charge (모달)
- config.py: BLINK_API_KEY, SPONTANEOUS_CHANNEL_ID 등
- requirements.txt: qrcode[pil]
- L1 perception.py: web_search 제거
- L2 tools.py: web_search, gift_user 추가
- L2 functioning.py: web_search 실행 (google_search_tool), gift 액션 처리
- L4 01_role.txt: 역할 축소 (원문 보존 우선)
- L2 05_instruction.txt: 무응답 제거, no_comment 추가
- core.py: 자발적 발화, 비멘션 응답, no_comment 처리, gift 후처리

---

## v5 (2026-04-03 ~)

v4 로그 분석 기반. 핵심 철학 변경: 고정 성격 제거, 감정 수치가 캐릭터를 만든다.

### 의도

핵심:
- v4까지 "밝고 활기찬 사서"로 성격 하드코딩 → 감정 수치가 있어도 캐릭터가 안 변함
- v5: 성격 정의 없음. 감정 수치가 캐릭터의 톤, 태도, 말수를 결정
- mood 높으면 수다스러운 사서, 낮으면 퉁명스러운 사서. 같은 사서인데 다른 사람처럼

5레이어 구조 (layers/):
- L1 Perception (temp 0.3): 맥락 관찰. 감정 수치를 자연어로 해석. 이전 소견 최우선 전달. 지시하지 않고 판단 재료만 넘김
- L2 Functioning (temp 0.5): 도구 실행. L1 결과 참조. 의도 분석은 안 함
- L3 Character (temp 1.2): 대사 생성. 역할만 정의, 성격은 L1이 넘긴 감정 해석에 의해 동적 결정
- L4 Postprocess (temp 0.1): 시스템 용어 제거, 자연어 정제. "쉿!" 같은 과잉 연출도 잡아냄
- L5 Evaluation (temp 0.3): 적합성 판단 + 감정 변화 + 소견. 명령이 아닌 우려 표현. 백그라운드

감정 시스템 v5:
- 6축: comfort(우정), affinity(관심/집착), trust(신뢰), self_mood(기분), self_energy(에너지), server_vibe(분위기)
- trust가 핵심: 다음 말을 믿을지 결정. 장난치면 하락, 진지하면 상승
- capacity: 소모 자원. 올리려면 명시적 회복만 인정. 응원은 mood지 capacity가 아님
- 각 축 독립적. affinity 높아도 trust 낮을 수 있음 (좋아하는데 말은 안 믿는 장난꾸러기)
- 변화량 확대: 일상 +-3-5, 사건 +-7-12, 극단 +-12-15
- 빈도 감쇠 완화: FREQ_COOLDOWN 1800s → 300s, 최소 30% 반영 보장
- 분산 유지 완화: TARGET_STD 15 → 20

검색:
- ChromaDB 벡터 검색 도입 (knowledge, learned, customs, book_knowledge)
- LIKE 검색 유지 (web_results, url_results, media_results, user_emotion)
- 유저 감정 검색 카테고리 추가

### 주요 변경

- 폴더: 1_processor/2_character/3_evaluator → layers/01-05
- processor → functioning (네이밍 일괄 변경)
- 반복 리트라이 구조 제거 (_is_repeat, 2차/3차 시도)
- 자동 이모지 따라누르기 제거 (on_raw_reaction_add)
- Character 프롬프트에서 성격 정의 전부 제거
- 감정 해석 가이드를 Perception에 배치
- Evaluation: "피드백" → "소견", 명령조 → 우려 표현
- Perception이 이전 소견을 최우선으로 전달

---

## v4 (2026-04-01 ~)

v3 로그 분석(3월 31일) 기반 전면 개선. 안정성 + 성능 + 기능 확장.

### 의도

안정성:
- INVALID_ARGUMENT 근절: 히스토리를 채널별 → 유저별로 분리, 채널 락 → 유저 락
- MAX_HISTORY 20 → 10 (5왕복), trim 시 function_call/response 쌍 보장
- INVALID_ARGUMENT 발생 시 히스토리 초기화 + 클린 재시도
- deliver/mood 텍스트 노출 방지: 인라인 함수 파싱 (positional args 포함, 재시도 응답에도 적용)
- mood 1회 적용 (_apply_mood 공통 함수)
- discord.py _ready 충돌 수정, typing 에러 처리

성능:
- _call_gemini 비동기화 (run_in_executor 내장) — 이벤트 루프 블로킹 근절
- URL 인식: pending → 백그라운드 처리 → done (유튜브 자막 우선)
- 도서 학습: 비동기 백그라운드, status 관리 (pending/done/failed)
- 미디어 인식: file_hash SHA-256 기반 중복 방지

기능:
- 도서 내용 학습 (epub/pdf → Gemini 요약 → book_knowledge → search)
- 환율 (업비트 KRW 시세 + USD/KRW + 김치 프리미엄)
- 날씨 (국내 8개 도시 + 국제 온디맨드)
- 뉴스 (국내/국제, search에서 조회)
- URL 인식 (recognize_link, Gemini에 URL 직접 전달)
- 미디어 첨부 (attach, librarian/media/ 저장)
- 유저 멘션 / 채널 링크 / 커스텀 이모지
- 감정 시스템 v4: feel 도구 + DB 기반 6축 (friendly/lovely/trust/self_mood/self_energy/server_vibe)
- feel의 reaction 파라미터 분리 (이모지 리액션과 텍스트 응답 혼동 방지)

로그/알림:
- bot.log [수신] 시점 로그 추가 (처리 시점과 구분)
- 어드민 에러 알림 대기열 (10초 내 모아서 1회 전송)
- DM 로그 UTF-8 BOM (한글 깨짐 방지)
- 직전 대화에 첨부/임베드/링크 정보 포함

검색:
- search: 7개 카테고리 (기억, 지식, 커스텀, 웹, URL, 미디어, 도서)
- 기억: 유저 10건 + 나머지 10건 (분리)
- 도서: 키워드 주변 200자 스니펫
- 반복 감지 임계값 0.8 → 0.9
- 프롬프트 웹/미디어 캐시 각 10건
- id 네이밍 정리 (entry_id, file_id 표기)

### 주요 변경

- 히스토리: 채널별 → 유저별, MAX_HISTORY 20 → 10
- _call_gemini: 동기 → async (run_in_executor)
- 도구 추가: recognize_link, attach
- 도서 학습: book_learning.py (epub ebooklib + Gemini, status 관리)
- url_results 테이블 분리 (web_results와 분리)
- media_results: file_hash, stored_name 추가
- 마이그레이션 011-019 추가
- feel 메타데이터 유출 방지: (feel: ...), /feel ..., feel(...) 등 5종 패턴 제거
- _strip_feeling 전 원본 로그 추가 ([1차] 원본:)
- [mood:XX] 레거시 폴백 정리 (시그니처 불일치로 호출 시 에러나던 코드 제거)

---

## v3 (2026-03-31 ~)

프로젝트 구조 전면 개편 + AI 판단 위임. v2 로그 분석을 기반으로 강제 트리거/필터를 전부 제거하고 모델에게 맡김.

### 의도

구조:
- 플랫 구조 → `library/` + `librarian/` 패키지 분리
- `config.json` (커밋) + `.env` (비밀값) 설정 분리
- `startup.py`를 config.json 기반으로 재작성, 이후 불변
- 런타임 데이터 격리: `data/`, `files/`, `logs/`
- `migrations/` + `patches/` 시스템

AI 단순화 (v2 로그 분석 기반):
- `_clean_reply`가 멘션/이모지를 삭제하는 문제 → 전체 제거
- 키워드 웹 검색이 맥락 없이 "잠깐만" 반복 → AI 자발 호출만, 루프 안에서 처리
- 기억 자동 저장이 쓰레기 학습의 원인 → AI 자발 save_memory만
- 채널 대화 30건이 잡담 모드 유도 → 답글 체인 + 직전 대화 10건으로 대체
- 3단계 리트라이 코드 중복 → 1-4차 구조로 정리
- 모델 업그레이드: gemini-2.5-flash-lite → gemini-3.1-flash-lite-preview

### 주요 변경

- 모델: gemini-3.1-flash-lite-preview (Intelligence 34)
- 단일 API 키 (멀티 키 로테이션 제거)
- 도서관 목록 + 기억을 프롬프트에 직접 포함 (도구 호출 대폭 감소)
- send_file → deliver (이름 변경)
- 도구 추가: forget_memory, modify_memory, recognize_media
- 도구 제거: get_entry_detail, list_entries
- 답글 체인 무제한 추적 (10건 초과 시 앞5+뒤5)
- 유저 ID를 맥락에 포함 (멘션 가능)
- 포워드 메시지 인식
- 페이지 시스템 (도서 분류/정렬)
- hidden/forgotten (soft delete)
- 웹 검색 결과 자동 학습 (learned에 저장)
- search: 지식+기억 통합 풀, 발화자 우선, 프롬프트 중복 제거
- 날짜별 로그 (bot.YYYY-MM-DD.log), TZ 기준 시간대
- 반복 감지: Jaccard 유사도 80%
- 히스토리 롤백 (루프 실패 시 스냅샷)
- 슬래시 커맨드 명시적 네이밍 (add_entry, add_file, edit_entry 등)

---

## v2 (f477477, 2026-03-30)

v1의 근본적 한계를 해결하기 위한 대규모 구조 개편. 같은 날 밤에 배포.

### 의도

- 단일 DB를 library.db + librarian.db로 분리
- 도구를 `search` 하나로 통합
- 비트코인 관련 지식 베이스 내장 (knowledge/*.txt, 120건)
- 프롬프트 분리 (persona.txt + prompt.txt + reminder.txt), 샌드위치 구조
- 기억 시스템 단순화 (learned 테이블 하나)
- 반복 방지 3단계 + Google Search grounding + API 키 5개 로테이션

### 주요 변경

- 모델: gemini-2.5-flash-lite (v1과 동일)
- DB 분리, 도구 통합, 지식 베이스 120건, 별칭 양방향 확장

---

## v1 (ce1da2e, 2026-03-29)

최초 배포. 비트쨩의 탄생.

### 의도

- 디스코드에서 비트코인 관련 자료를 공유하고 AI 사서가 안내하는 시스템
- 라이브러리 봇 + AI 사서봇 분리, startup.py로 동시 관리

### 구성

- 모델: gemini-2.5-flash-lite, 단일 API 키, 단일 DB
- 도구 4개, 페르소나 하드코딩, 반복 방지/웹 검색/지식 베이스 없음
