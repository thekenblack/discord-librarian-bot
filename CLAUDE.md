# Discord Librarian Bot — 기술 문서

## 프로젝트 의도

비트코인 맥시멀리스트 커뮤니티(시타델)를 위한 디스코드 도서관 봇.
두 가지 봇이 하나의 도서관을 함께 운영한다.

### 라이브러리 봇 (library/)

슬래시 커맨드 기반의 자료 관리 시스템.

- 책장(엔트리) 생성/편집/숨기기
- 파일 업로드/편집/숨기기
- 페이지 관리 (분류/정렬)
- 어드민 관리 (봇 중지/재개, 업데이트, 백업, 통계)
- 누구나 자료를 등록할 수 있고, 자기가 올린 것은 직접 편집 가능

### AI 사서봇 (librarian/)

Gemini 기반 대화형 사서. 멘션하면 반응한다. 시타델 도서관의 "사람" 역할.

- 도서관 목록을 프롬프트에 직접 포함 — 도구 호출 없이 즉시 안내 가능
- 자연어로 파일 전송 (deliver)
- 비트코인 관련 지식 내장 (knowledge/*.txt)
- 유저가 가르치면 기억 (learned 테이블, AI 자발 저장)
- 기억 수정/삭제 (modify_memory, forget_memory — soft delete)
- 웹 검색(Google Search grounding) + 결과 자동 학습
- 이미지/PDF 인식 (recognize_media — 멀티모달)
- 답글 체인 추적 (무제한 깊이, 10건 초과 시 앞5+뒤5)
- 반복 감지 + 리트라이 (1-4차)
- 페르소나: 비트코인 맥시멀리스트, 반말, 이모지, 유머

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
- `logs/` — 날짜별 로그 bot.YYYY-MM-DD.log, server.YYYY-MM-DD.log (gitignore)
- 서버 이전 시 data/ + files/ 만 옮기면 된다

### 마이그레이션 vs 패치

**마이그레이션** (`migrations/`, 커밋됨) — 스키마 변경. 모든 서버에 적용.
- startup.py가 `data/migrations_applied.json`으로 추적

**패치** (`patches/`, 커밋됨) — 데이터 수정. git pull로 전달됨.
- `.py` 파일: 조건 검사 후 실행 (다른 유저 DB 보호)
- startup.py가 `data/patches_applied.json`으로 추적
- soft delete (forgotten) 사용, 실제 삭제 없음

## AI 사서봇 상세

### 프롬프트 구조

```
페르소나 (persona.txt)
규칙 + 도서관 목록 + 기억 (prompt.txt)
상황 정보 (시간, 유저, 관리자)
직전 대화 (답글 체인 시작점 직전 10건)
답글 흐름 (체인, 무제한 → 10건 초과 시 앞5+뒤5)
리마인더 (reminder.txt)
페르소나 (반복)
```

페르소나를 앞뒤로 반복(샌드위치)해서 캐릭터 이탈 방지.

### 도서관 목록을 프롬프트에 직접 포함

매 요청마다 DB에서 도서관 목록을 빌드해서 프롬프트에 삽입.
AI가 "뭐 있어?"에 도구 호출 없이 바로 답변. "모위비 줘"에 deliver 1회로 끝.
파일 없는 엔트리, hidden 엔트리는 제외.

### 기억 시스템

프롬프트에 포함:
- 발화자(현재 유저) 기억 최근 10건
- 나머지 최근 10건 (발화자 제외)

search 도구:
- 발화자 우선 10건 + 통합(지식+기억) 10건
- 프롬프트에 이미 포함된 기억은 제외 (ID 기반)

저장:
- AI가 save_memory 자발 호출 시만 저장 (author 포함)
- 최대 100건, 초과 시 오래된 것부터 삭제

삭제/수정:
- forget_memory — soft delete (forgotten = 1)
- modify_memory — forget + save

### 도구 목록

| 도구 | 용도 |
|---|---|
| deliver(file_id) | 파일 전송 |
| search(keyword) | 지식+기억 통합 검색 (발화자 우선) |
| web_search(query) | 웹 검색 + 결과 자동 저장 |
| save_memory(content) | 기억 저장 |
| forget_memory(keyword) | 기억 잊기 (soft delete) |
| modify_memory(keyword, new_content) | 기억 수정 |
| add_alias(name, alias) | 별칭 등록 |
| add_entry_alias(entry_id, alias) | 엔트리 별칭 추가 |
| recognize_media(attachment_index) | 이미지/PDF 인식 |

### 리트라이 구조

```
1차: 히스토리 + 전체 프롬프트 (0.8)
  → 도구 루프 최대 10회
  → 반복/빈 응답 시 ↓
2차: 클린 (유저 메시지만, 맥락 제거, 기억 유지) (0.9)
  → 반복/빈 응답 시 ↓
3차: bare (기억도 제거, 도서관만) + 웹 검색 (1.0)
  → 반복/빈 응답 시 ↓
4차: 포기 → 에러 메시지
```

반복 감지: 단어 기반 Jaccard 유사도 80% (이모지/구두점 제거 후 비교).
히스토리 롤백: 루프 실패 시 스냅샷으로 복구.
2-3차는 1회성 히스토리 (원본 오염 없음).

### API 호출

단일 API 키. 재시도 3회, 1초 간격. INVALID_ARGUMENT는 즉시 raise.

### 로그

- `bot.YYYY-MM-DD.log` — 상세 단계별 추적 ([1차], [2차] 등)
- `server.YYYY-MM-DD.log` — 서버 전체 메시지 (멘션 무관)
- 시간대: .env TZ 기준 (기본 Asia/Seoul, +09:00 표시)

## 슬래시 커맨드

### library

| 커맨드 | 설명 |
|---|---|
| /library list | 도서관 목록 (페이지네이션) |
| /library info | 엔트리 상세 + 다운로드 |
| /library share | 엔트리 정보 채널에 공유 |
| /library add_entry | 새 엔트리 생성 |
| /library add_file | 파일 업로드 |
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
