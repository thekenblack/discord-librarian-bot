# Discord Librarian Bot -- 기술 문서

## 프로젝트 의도

비트코인 맥시멀리스트 커뮤니티(시타델)를 위한 디스코드 도서관 봇.
두 가지 봇이 하나의 도서관을 함께 운영한다.

### 라이브러리 봇 (library/)

슬래시 커맨드 기반의 자료 관리 시스템.

- 책장(엔트리) 생성/편집/숨기기
- 파일 업로드/편집/숨기기 + 도서 자동 학습
- 페이지 관리 (분류/정렬)
- 어드민 관리 (봇 중지/재개, 업데이트, 백업, 통계, 로그)
- 누구나 자료를 등록할 수 있고, 자기가 올린 것은 직접 편집 가능

### AI 사서봇 (librarian/)

Gemini 기반 대화형 사서. 멘션하면 반응한다. 시타델 도서관의 "사람" 역할.

핵심 철학 (v5): 고정 성격 없음. 감정 수치가 캐릭터를 만든다.
- mood 높으면 수다스러운 사서, 낮으면 퉁명스러운 사서
- comfort 높은 유저에겐 편하게, 낮은 유저에겐 어색하게
- trust 낮으면 상대 말을 농담으로 취급

### 왜 봇이 2개인가

- 라이브러리 봇은 "시스템" -- 구조적 CRUD, 슬래시 커맨드, 권한 체크
- AI 사서봇은 "캐릭터" -- 자연어 대화, 기억, 판단
- 역할이 다르고 토큰도 다르다. 하나가 죽어도 다른 하나는 살아있다
- startup.py가 둘을 동시에 관리하고, exit code 42로 전체 재시작을 트리거

## 아키텍처

### 5레이어 구조 (librarian/layers/)

```
L1 Perception (temp 0.3, 채널별 히스토리 10건)
  맥락 관찰. 감정 수치를 자연어로 해석. 이전 소견 최우선 전달.
  유저/채널 요약(DB)을 읽어서 장기 맥락 반영.
  지시하지 않음. 판단 재료만 넘김.
  도구 없음.
      |
L2 Functioning (temp 0.5, 히스토리 없음)
  도구 실행. L1 결과 참조. 도구 1회 제한 + 루프 10회 제한.
  도구: search, deliver, attach, web_search, recognize_media, recognize_link, memorize_alias, forget_alias
      |
L3 Character (temp 1.2, 유저별 히스토리 10건)
  대사 생성. 역할만 정의, 성격은 L1이 넘긴 감정 해석에 의해 동적 결정.
  도구 없음.
      |
L4 Postprocess (temp 0.1, 히스토리 없음)
  시스템 흔적 교체, 멘션/채널 교정, 대사 보완.
  도구 없음.
      |
L5 Evaluation (temp 0.3, 단일 히스토리 10건, 백그라운드)
  적합성 판단 + 감정 변화 + 소견 + 유저/채널 요약 갱신.
  도구: feel, memorize, forget, update_summary, update_channel_summary
  전체 맥락(L1+L2 결과) + 이전 요약 받음. 명령이 아닌 우려 표현.
```

유저 체감 대기: L1 -> L2 -> L3 -> L4 (직렬 4콜). L5는 응답 전송 후 백그라운드.

### 설정 분리

- `config.json` (커밋) -- 프로젝트 구조, 모델명, 경로, 봇 목록
- `.env` (비밀) -- 토큰, API 키, 어드민 ID
- config.json을 바꾸면 구조가 바뀌고, .env를 바꾸면 환경이 바뀐다

### startup.py는 불변

config.json에서 봇 목록을 읽으므로 수정할 필요가 없다.

하는 일:
1. venv 자동 생성 + 매 시작 시 pip install
2. 구 구조에서 신 구조로 파일 마이그레이션 (1회성)
3. migrations/ 스크립트 실행 (미적용분만)
4. patches/ 스크립트 실행 (미적용분만)
5. DB 백업 (최근 5개 유지)
6. 봇 프로세스 관리 (재시작 신호 42 -> git pull -> pip install -> 전체 재시작)

### 데이터 격리

- `data/` -- DB 파일 (library.db, librarian.db) + chroma/ (벡터 DB) + 백업 (gitignore)
- `files/` -- 업로드된 실제 파일 (gitignore)
- `librarian/media/` -- 인식된 미디어 파일 (gitignore)
- `logs/` -- 날짜별 로그 bot.YYYY-MM-DD.log, server.YYYY-MM-DD.log (gitignore)
- 서버 이전 시 data/ + files/ + librarian/media/ 를 옮기면 된다

### 마이그레이션 vs 패치

마이그레이션 (`migrations/`, 커밋됨) -- 스키마 변경. 모든 서버에 적용.
- startup.py가 `data/migrations_applied.json`으로 추적
- 폴더명: `{순번}_{커밋해시}/` (서버 배포 기준)
- 반드시 동기 sqlite3 + config.json 직접 읽기 패턴으로 작성 (async/aiosqlite/from config import 사용 금지)

패치 (`patches/`, 커밋됨) -- 데이터 수정. git pull로 전달됨.
- `.py` 파일: 조건 검사 후 실행 (다른 유저 DB 보호)
- startup.py가 `data/patches_applied.json`으로 추적
- soft delete (forgotten) 사용, 실제 삭제 없음

## AI 사서봇 상세

### 레이어별 프롬프트

각 레이어의 `prompts/` 디렉토리 내 .txt 파일을 파일명 순서로 합침 (persona.py).

L1 Perception:
- 01_role.txt -- 관찰자 역할, 이전 소견 최우선, 지시 금지
- 02_emotion.txt -- 감정 수치 해석 가이드 (6축, 각 축 독립성 명시)

L2 Functioning:
- 01_role.txt -- 도구 실행기 역할
- 02_library.txt -- 도서관 카탈로그 + 기억 ({library_catalog}, {learned_memories})
- 03_tools.txt -- 도구 설명
- 05_instruction.txt -- 출력 형식 (응답 모드, 도구 결과 보고)

L3 Character:
- 01_persona.txt -- 역할만 (시타델 도서관 사서). 성격 없음
- 02_appearance.txt -- 외양
- 03_behavior.txt -- 출력 규칙 + 컨텍스트 활용법. 성격/태도 지시 없음

L4 Postprocess:
- 01_role.txt -- 대사 다듬기, 시스템 흔적 교체, 멘션/채널 교정, 대사 보완

L5 Evaluation:
- 01_role.txt -- 평론가 역할, 적합성 판단, 소견은 우려 표현, 요약 도구 안내
- 02_emotion.txt -- 6축 감정 판정 기준 + 변화량 가이드
- 03_feedback.txt -- 소견 작성 규칙 + 요약과의 역할 분담

### 히스토리

레이어별 히스토리 스코프:
- L1 Perception: 채널별 히스토리 (channel_id -> history). MAX=10
- L2 Functioning: 히스토리 없음. 단발 호출. 도구 1회 제한 + 루프 10회 제한
- L3 Character: 유저별 히스토리 (user_id -> history). MAX=10. trim 시 function_call/response 쌍 보장
- L4 Postprocess: 히스토리 없음
- L5 Evaluation: 단일 히스토리 (전 채널/유저 공유). MAX=10

유저 락 (asyncio.Lock)으로 같은 유저 동시 요청 직렬화

### 대화 요약

- user_summary: 유저별 대화 요약 (DB). L5가 매 턴 update_summary로 갱신, L1이 읽음
- channel_summary: 채널별 흐름 요약 (DB). L5가 매 턴 update_channel_summary로 갱신, L1이 읽음
- 히스토리가 trim으로 밀려도 요약이 장기 맥락을 보존

### 도서 학습

- 파일 업로드 시 + 사서봇 시작 시 미학습 도서 자동 처리
- epub: ebooklib 텍스트 추출 (toc.ncx 없으면 zipfile 폴백) -> Gemini 요약
- pdf/txt: Gemini에 바이너리 직접 전달
- status: pending -> done/failed. 재시작 시 pending/failed 정리
- search에서 done만 검색

### 도구 목록

Functioning(L2) 도구:
| 도구 | 용도 |
|---|---|
| search(keyword) | 지식+기억+도서+웹+미디어+유저감정 통합 검색. 뉴스/날씨도 가능 |
| deliver(file_id) | 파일 전송 |
| attach(media_id 또는 url_id) | 저장된 미디어/URL 첨부 전송 |
| memorize_alias(name, alias) | 별칭 등록 (검색 시 자동 확장) |
| forget_alias(alias_id) | 별칭 삭제 |
| web_search(query) | 웹 검색 + 결과 자동 저장 |
| recognize_media(attachment_index) | 이미지/PDF 인식 (file_hash 중복 방지) |
| recognize_link(url) | URL 인식 (이미지 URL은 동기, 나머지 백그라운드) |

Evaluation(L5) 도구:
| 도구 | 용도 |
|---|---|
| feel(...) | 감정 변화 기록. 6축, reason 필수 |
| memorize(content) | 유저가 알려준 사실 기억 |
| forget(keyword) | 잘못된 기억 삭제 (soft delete) |
| update_summary(summary) | 유저별 대화 요약 갱신 (매 턴) |
| update_channel_summary(summary) | 채널 흐름 요약 갱신 (매 턴) |

feel, memorize, forget은 1요청당 1회만 호출 가능. update_summary, update_channel_summary는 매 턴 호출.

### 검색 시스템

벡터 검색 (ChromaDB, 의미 매칭):
- knowledge (기초 지식)
- learned (유저 기억)
- customs (커스텀 지식)
- book_knowledge (도서 학습)

LIKE 검색 (정확 매칭):
- web_results (웹 검색 캐시)
- url_results (URL 인식 캐시)
- media_results (미디어 인식 캐시)
- user_emotion (유저 감정)

벡터 검색 실패 시 LIKE로 폴백. ChromaDB는 data/chroma/에 저장.
시작 시 SQLite와 건수 비교 후 자동 동기화.

### 감정 시스템

6축, DB 기반, EMA 감쇠:

유저별 (0-100, 중립 50):
- comfort -- 우정. 편한 정도. 천천히 쌓이고 천천히 빠짐
- affinity -- 관심/집착. comfort 낮아도 높을 수 있음
- trust -- 핵심 축. 다음 말을 믿을지 결정. 진지하면 상승, 장난치면 하락

봇 전체:
- self_mood -- 기분. 캐릭터 톤을 직접 결정
- self_energy -- 에너지. 가벼운 대화는 회복, 복잡한 대화는 소모

서버:
- server_vibe -- 분위기. 서버 활기

변화량 가이드: 일상 +-3-5, 사건 +-7-12, 극단 +-12-15
AXIS_DELTA_MAX = 15, FREQ_COOLDOWN = 300초, 최소 30% 반영
시간 감쇠: 중립(50)을 향해 반감기 기반 복귀 (user 48h, self 6h, server 12h)
emotion_log 테이블에 변경 이력 기록

### 에러 처리

- ClientError RESOURCE_EXHAUSTED: 에러 메시지 반환
- ClientError INVALID_ARGUMENT: 히스토리 초기화 + 클린 재시도
- L5 Evaluation 에러: 무시 (응답에 영향 없음)
- L4 Postprocess 실패: 원본 대사 사용

### API 호출

- 단일 API 키
- `_call_gemini`: async (run_in_executor 내장), 재시도 3회, 1초 간격
- 이벤트 루프 블로킹 없음

### 로그

- `bot.YYYY-MM-DD.log` -- 레이어별 타이밍 ([L1 Perception], [L2 Functioning], [Character], [Postprocess], [Evaluator])
- `server.YYYY-MM-DD.log` -- 서버 전체 메시지 (멘션 무관)
- 시간대: .env TZ 기준 (기본 Asia/Seoul)
- 어드민 에러 알림: 대기열 10초, 모아서 DM 1회

## 슬래시 커맨드

### library

| 커맨드 | 설명 |
|---|---|
| /library list | 도서관 목록 (페이지네이션) |
| /library info | 엔트리 상세 + 다운로드 |
| /library share | 엔트리 정보 채널에 공유 |
| /library add_entry | 새 엔트리 생성 |
| /library add_file | 파일 업로드 (+ 도서 자동 학습) |
| /library edit_entry | 내가 만든 엔트리 편집 |
| /library edit_files | 내가 올린 파일 편집 |
| /help | 명령어 도움말 |
| /donate | 라이트닝 후원 |

### admin

| 커맨드 | 설명 |
|---|---|
| /admin stop | 봇 일시 중지 |
| /admin resume | 봇 재개 |
| /admin update | git pull + 재시작 |
| /admin backup | DB 백업 DM 전송 |
| /admin stats | 운영 현황 |
| /admin log | 로그 파일 DM 전송 |
| /admin edit_entry | 전체 엔트리 편집/숨기기 |
| /admin edit_files | 전체 파일 편집/숨기기 |
| /admin hide_entry | 엔트리 숨기기/보이기 |
| /admin add_page | 페이지 추가 |
| /admin pages | 페이지 관리 (편집/숨기기) |
| /admin page | 엔트리 페이지 배정 |

## 네이밍 규칙

- `library` = 도서관 시스템 (책장, 파일, 슬래시 커맨드)
- `librarian` = AI 사서 (대화, 기억, Gemini)
- `layers` = 5단계 AI 처리 레이어 (01_perception ~ 05_evaluation)
- 책장 = 자료의 틀 (유저 향 용어). 내부적으로는 entry/books
- 파일 = 책장 안의 실제 자료 (epub 등). 책장만 있고 파일이 없을 수 있음
- hidden = 숨김 (soft delete, DB에 남아있음)
- forgotten = 잊힘 (기억의 soft delete)

## 버전 관리

- `config.json`의 `version` 필드가 현재 버전
- `CHANGELOG.md`에 버전별 의도와 주요 변경사항 기록
- `FEEDBACKS.md`에 배포 후 실제 동작 피드백 기록
- 시작 시 `bot.log`에 version + git hash + model 기록
- 버전을 올릴 때: config.json version 변경 + CHANGELOG.md 항목 추가

## 배포

- 서버: Linux, startup.py로 실행
- 업데이트: `/admin update` -> exit 42 -> git pull -> pip install -> 전체 재시작
- 초기 설치: `cp env.example .env` -> 값 입력 -> `python startup.py`

## 작성 규칙

- 커밋된 파일에 유저 닉네임, ID, 대화 내용 등 개인 정보를 직접 포함하지 않는다
- FEEDBACKS.md 등에 피드백을 기록할 때는 추상적으로 작성한다 (예: "에러 노출에 대한 불만 다수")
- 구체적인 유저 데이터는 DB와 로그에만 존재한다 (gitignore)
- 마크다운에 볼드체 사용하지 않는다. 로우 텍스트로 읽을 것을 전제한다
- 프롬프트에 부정형("하지 마", "~하지 않는다") 대신 긍정형 대안을 제시한다. 부정형은 모델이 잘 안 따른다
