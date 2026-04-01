# Discord Librarian Bot — 기술 문서

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

- 도서관 목록을 프롬프트에 직접 포함 — 도구 호출 없이 즉시 안내 가능
- 도서 내용 학습 (epub/pdf → Gemini 요약 → book_knowledge, search에 연결)
- 자연어로 파일 전송 (deliver)
- 비트코인 관련 지식 내장 (knowledge/*.txt)
- 유저가 가르치면 기억 (learned 테이블, AI 자발 저장)
- 기억 수정/삭제 (modify_memory, forget_memory — soft delete)
- 웹 검색(Google Search grounding) + 결과 자동 학습
- URL 인식 (recognize_link — Gemini에 URL 직접 전달, 유튜브 자막 우선)
- 이미지/PDF 인식 (recognize_media — 멀티모달) + 미디어 첨부 (attach)
- 실시간 비트코인 현황, 환율(KRW), 김프, 날씨(8개 도시), 뉴스
- 답글 체인 추적 (무제한 깊이, 10건 초과 시 앞5+뒤5)
- 반복 감지 + 리트라이 (반복만 재시도)
- EMA 기반 감정 시스템 (상대값 +/-, cap=15)
- 유저 멘션 / 채널 링크 / 커스텀 이모지
- 어드민 에러 알림 (대기열 방식, 10초 내 모아서 1회)
- 페르소나: 비트코인 맥시멀리스트, 반말, 위트+쿨

### 왜 봇이 2개인가

- 라이브러리 봇은 "시스템" — 구조적 CRUD, 슬래시 커맨드, 권한 체크
- AI 사서봇은 "캐릭터" — 자연어 대화, 성격, 기억, 판단
- 역할이 다르고 토큰도 다르다. 하나가 죽어도 다른 하나는 살아있다
- startup.py가 둘을 동시에 관리하고, exit code 42로 전체 재시작을 트리거

## 아키텍처

### 설정 분리

- `config.json` (커밋) — 프로젝트 구조, 모델명, 경로, 봇 목록
- `.env` (비밀) — 토큰, API 키, 어드민 ID
- config.json을 바꾸면 구조가 바뀌고, .env를 바꾸면 환경이 바뀐다

### startup.py는 불변

config.json에서 봇 목록을 읽으므로 수정할 필요가 없다.

하는 일:
1. venv 자동 생성 + 매 시작 시 pip install
2. 구 구조에서 신 구조로 파일 마이그레이션 (1회성)
3. migrations/ 스크립트 실행 (미적용분만)
4. patches/ 스크립트 실행 (미적용분만)
5. DB 백업 (최근 5개 유지)
6. 봇 프로세스 관리 (재시작 신호 42 → git pull → pip install → 전체 재시작)

### 데이터 격리

- `data/` — DB 파일 (library.db, librarian.db) + 백업 (gitignore)
- `files/` — 업로드된 실제 파일 (gitignore)
- `librarian/media/` — 인식된 미디어 파일 (gitignore)
- `logs/` — 날짜별 로그 bot.YYYY-MM-DD.log, server.YYYY-MM-DD.log (gitignore)
- 서버 이전 시 data/ + files/ + librarian/media/ 를 옮기면 된다

### 마이그레이션 vs 패치

마이그레이션 (`migrations/`, 커밋됨) — 스키마 변경. 모든 서버에 적용.
- startup.py가 `data/migrations_applied.json`으로 추적
- 반드시 동기 sqlite3 + config.json 직접 읽기 패턴으로 작성 (async/aiosqlite/from config import 사용 금지 — startup.py가 subprocess로 실행하기 때문)

패치 (`patches/`, 커밋됨) — 데이터 수정. git pull로 전달됨.
- `.py` 파일: 조건 검사 후 실행 (다른 유저 DB 보호)
- startup.py가 `data/patches_applied.json`으로 추적
- soft delete (forgotten) 사용, 실제 삭제 없음

## AI 사서봇 상세

### 프롬프트 구조

```
페르소나 (persona.txt)
규칙 + 도서관 목록 + 기억 (functioning.txt)
상황 정보 (시간, 유저, 관리자)
비트코인 현황 + 환율 + 김프 + 국내 날씨
감정 상태
직전 대화 (답글 체인 시작점 직전 10건, 첨부/링크 설명 포함)
답글 흐름 (체인, 무제한 → 10건 초과 시 앞5+뒤5)
최근 인식은 프롬프트에 미포함 (토큰 절약). search 도구로 접근
리마인더 (reminder.txt)
캐릭터 (character.txt)
```

### 히스토리

- 유저별 관리 (user_id → history). 채널별 아님.
- 유저 락 (asyncio.Lock)으로 같은 유저 동시 요청 직렬화
- MAX_HISTORY = 10 (5왕복)
- trim 시 function_call/response 쌍 보장

### 도서 학습

- 파일 업로드 시 + 사서봇 시작 시 미학습 도서 자동 처리
- epub: ebooklib 텍스트 추출 (toc.ncx 없으면 zipfile 폴백) → Gemini 요약
- pdf/txt: Gemini에 바이너리 직접 전달
- status: pending → done/failed. 재시작 시 pending/failed 정리
- search에서 done만 검색, 키워드 주변 200자 스니펫

### 도구 목록

| 도구 | 용도 |
|---|---|
| search(keyword) | 지식+기억+도서+웹+미디어 통합 검색. 뉴스/날씨도 가능 |
| deliver(file_id) | 파일 전송 |
| attach(media_id 또는 url_id) | 저장된 미디어/URL 첨부 전송 |
| memorize(content) | 기억 저장. 수정이 필요하면 forget 후 memorize |
| forget(keyword) | 기억 잊기 (soft delete) |
| memorize_alias(name, alias) | 별칭 등록 (검색 시 자동 확장) |
| forget_alias(alias_id) | 별칭 삭제 |
| web_search(query) | 웹 검색 + 결과 자동 저장 |
| recognize_media(attachment_index) | 이미지/PDF 인식 (file_hash 중복 방지) |
| recognize_link(url) | URL 인식 (이미지 URL은 동기, 나머지 백그라운드) |
| feel(...) | 감정 변화 기록 |

모든 도구는 1요청당 1회만 호출 가능. 사용한 도구는 다음 API 호출에서 목록에서 제거됨.

### 리트라이 구조

```
1차: 유저별 히스토리 + 전체 프롬프트 (0.8)
  → 도구 루프 최대 10회
  → 반복 감지 시 (Jaccard 0.9) ↓
2차: 클린 (유저 메시지만, 맥락 제거, 기억 유지) (0.9)
  → 여전히 반복 시 ↓
3차: bare (기억도 제거, 도서관만) + 웹 검색 (1.0)
  → 여전히 반복 시 → 포기

빈 응답: 즉시 에러 메시지 (재시도 안 함)
INVALID_ARGUMENT: 히스토리 초기화 + 도구 없이 1회 재시도
```

인라인 함수 감지: 2-3차 재시도 응답에도 deliver(5) 같은 패턴 감지 → 실행 + 텍스트 제거.

### 감정 시스템

- [mood:+12] 상대값: cap=±15 → EMA(α=0.08) 적용
- [mood:60] 절대값: EMA(α=0.08) 적용
- 시간 감쇠 τ=21600s (6시간)
- 한 요청당 1회만 적용 (_apply_mood)
- 혼자일 때 원점수 그대로

### API 호출

- 단일 API 키
- `_call_gemini`: async (run_in_executor 내장), 재시도 3회, 1초 간격
- 이벤트 루프 블로킹 없음

### 로그

- `bot.YYYY-MM-DD.log` — [수신] 시점 + 상세 단계별 추적 ([1차], [2차] 등)
- `server.YYYY-MM-DD.log` — 서버 전체 메시지 (멘션 무관)
- 시간대: .env TZ 기준 (기본 Asia/Seoul)
- 어드민 에러 알림: 대기열 10초, 모아서 DM 1회 (UTF-8 BOM)

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
- 업데이트: `/admin update` → exit 42 → git pull → pip install → 전체 재시작
- 초기 설치: `cp env.example .env` → 값 입력 → `python startup.py`

## 작성 규칙

- 커밋된 파일에 유저 닉네임, ID, 대화 내용 등 개인 정보를 직접 포함하지 않는다
- FEEDBACKS.md 등에 피드백을 기록할 때는 추상적으로 작성한다 (예: "에러 노출에 대한 불만 다수")
- 구체적인 유저 데이터는 DB와 로그에만 존재한다 (gitignore)
- 마크다운에 볼드체(**) 사용하지 않는다. 로우 텍스트로 읽을 것을 전제한다
- 프롬프트에 부정형("하지 마", "~하지 않는다") 대신 긍정형 대안을 제시한다. 부정형은 모델이 잘 안 따른다
