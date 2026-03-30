# Discord Librarian Bot

## 프로젝트 의도

비트코인 맥시멀리스트 커뮤니티(시타델)를 위한 디스코드 도서관 봇.
두 가지 봇이 하나의 도서관을 함께 운영한다.

### 라이브러리 봇 (library/)

슬래시 커맨드 기반의 자료 관리 시스템. 정확하고 구조적인 조작을 담당한다.

- 엔트리(서지) 생성/편집/삭제
- 파일 업로드/다운로드/편집/삭제
- 어드민 관리 (봇 중지/재개, 업데이트, 백업, 통계)
- 누구나 자료를 등록할 수 있고, 자기가 올린 것은 직접 편집 가능

### AI 사서봇 (librarian/)

Gemini 기반 대화형 사서. 멘션하면 반응한다. 시타델 도서관의 "사람" 역할.

- 자연어로 자료를 검색하고 파일을 전송해줌 (function calling)
- 비트코인 관련 기초지식을 내장하고 있음 (knowledge/*.txt)
- 유저가 가르치면 기억함 (learned 테이블)
- 웹 검색(Google Search grounding)으로 최신 정보 제공
- 페르소나가 있음: 비트코인 맥시멀리스트, 반말, 이모지, 유머

### 왜 봇이 2개인가

- 라이브러리 봇은 "시스템" — 구조적 CRUD, 슬래시 커맨드, 권한 체크
- AI 사서봇은 "캐릭터" — 자연어 대화, 성격, 기억, 판단
- 역할이 다르고 토큰도 다르다. 하나가 죽어도 다른 하나는 살아있다.
- startup.py가 둘을 동시에 관리하고, exit code 42로 전체 재시작을 트리거한다.

## 아키텍처 원칙

### 설정 분리

- `config.json` (커밋) — 프로젝트 구조, 모델명, 경로, 봇 목록
- `.env` (비밀) — 토큰, API 키, 어드민 ID
- config.json을 바꾸면 구조가 바뀌고, .env를 바꾸면 환경이 바뀐다.

### startup.py는 불변

startup.py는 config.json에서 봇 목록을 읽으므로, 봇을 추가/제거해도 startup.py를 수정할 필요가 없다. 서버에서 SSH 재접속 없이 `/admin update`만으로 변경을 적용하려면 startup.py가 안정적이어야 한다.

startup.py가 하는 일:
1. venv 자동 생성 + 매 시작 시 pip install
2. 구 구조에서 신 구조로 파일 마이그레이션 (1회성, 자동)
3. migrations/ 스크립트 실행 (미적용분만, 추적 파일로 관리)
4. DB 백업 (최근 5개 유지)
5. 봇 프로세스 관리 (재시작 신호 42 → git pull → pip install → 전체 재시작)

### 데이터 격리

- `data/` — DB 파일 + 백업 (gitignore)
- `files/` — 업로드된 실제 파일 (gitignore)
- `logs/` — bot.log (gitignore)
- 서버 이전 시 data/ + files/ 만 옮기면 된다.

### 마이그레이션 vs 패치

**마이그레이션** (`migrations/`, 커밋됨) — 스키마 변경. 모든 서버에 적용.
- `migrations/001_xxx.py` → startup.py가 `data/migrations_applied.json`으로 추적

**패치** (`data/patches/`, gitignore) — 데이터 수정. 그 서버에서만 실행.
- `.sql` 파일: 파일명이 `library_`로 시작하면 library.db, 그 외는 librarian.db에 실행
- `.py` 파일: Python 스크립트로 실행
- startup.py가 `data/patches_applied.json`으로 추적
- 예: 쓰레기 학습 삭제, 잘못된 별칭 수정, 특정 데이터 정리

패치 예시:
```sql
-- data/patches/librarian_001_cleanup.sql
DELETE FROM learned WHERE content LIKE '%쓰레기%';
DELETE FROM learned WHERE content LIKE '%[원본:%';
```

## AI 사서봇 설계 의도

### 프롬프트 샌드위치

시스템 프롬프트를 `페르소나 → 규칙 → 맥락 → 리마인더 → 페르소나` 순으로 조립한다. 페르소나를 앞뒤로 반복해서 캐릭터 이탈을 방지한다.

### 도구 우선 설계

"질문이 오면 search를 먼저 호출해" — AI가 자체 지식으로 답하기 전에 항상 도서관 데이터를 확인하도록 유도한다. 도서관 봇이지 범용 챗봇이 아니다.

### 기억 시스템

- AI가 function calling으로 자발적으로 저장 (save_memory)
- 유저가 "기억해" 등 트리거 키워드 사용 시 자동 저장
- "~은/는 ~이야/다" 같은 설명식 문장도 자동 저장
- 저장된 기억은 search 도구로 검색되어 답변에 활용됨

### 별칭 확장

검색 시 별칭을 양방향으로 확장한다. "사토시"를 검색하면 "Satoshi Nakamoto"도 함께 검색된다. knowledge 파일의 `| alias1, alias2` 구문과 유저가 등록한 별칭 모두 적용.

### API 키 로테이션

무료/저비용 Gemini 키를 여러 개 등록하여 분당/일일 한도를 분산한다. 한도 초과 시 해당 키를 하루 동안 비활성화하고 다음 키로 자동 전환.

### 반복 방지

이전 답변과 동일한 응답이 나오면 맥락을 줄이고 temperature를 올려 재시도한다. 3단계(기본 → 맥락 제거 → 웹 검색 폴백)까지 시도한 후 실패하면 에러 메시지를 출력한다.

## 네이밍 규칙

- `library` = 도서관 시스템 (엔트리, 파일, 슬래시 커맨드)
- `librarian` = AI 사서 (대화, 기억, Gemini)
- 엔트리 = 서지 레코드 (메타데이터 틀)
- 파일 = 실제 자료 (epub 등, 엔트리에 종속)

## 버전 관리

- `config.json`의 `version` 필드가 현재 버전
- `CHANGELOG.md`에 버전별 주요 변경사항 기록 — **기능 추가/변경/삭제 시 반드시 업데이트**
- 시작 시 `logs/chat.jsonl`에 version + git hash + model + prompt_hash 기록
- 대화마다 version, model, 도구 호출, 에러 등이 JSON으로 기록됨
- 버전을 올릴 때: config.json version 변경 + CHANGELOG.md 항목 추가

## 배포

- 서버: Linux, startup.py로 실행
- 업데이트: `/admin update` → exit 42 → git pull → pip install → 전체 재시작
- 초기 설치: `cp env.example .env` → 값 입력 → `python startup.py`
