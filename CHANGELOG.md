# Changelog

배포 당시 의도를 기록한다. 배포 후 실제 동작은 FEEDBACKS.md에 기록.

---

## v3 (2026-03-30)

프로젝트 구조 전면 개편. 유지보수 편의성과 버전 관리 체계 확립이 목적.

### 의도

- 플랫 구조를 `library/` + `librarian/` 패키지로 분리해서 역할별 코드 경계를 명확히 하고 싶었음
- `config.json` (커밋) + `.env` (비밀값) 분리로 모델/경로 변경을 코드 변경 없이 하고 싶었음
- `startup.py`를 한 번만 고쳐서 이후 절대 안 건드려도 되게 만들고 싶었음
- 런타임 데이터(DB, 파일, 로그)를 코드와 격리해서 서버 이전 시 data/ + files/ 만 옮기면 되게
- `migrations/` 시스템으로 DB 스키마 변경을 `/admin update`만으로 적용하고 싶었음
- 버전별 비교 분석을 위해 구조화된 대화 로그(`logs/chat.jsonl`) 도입

### 주요 변경

- 모델: gemini-2.5-flash-lite → gemini-2.5-flash
- 네이밍: `ai/` → `librarian/`, `uploads/` → `files/`, `ai_bot.log` → `logs/bot.log`
- 구 구조에서 신 구조로 파일 자동 마이그레이션 (startup.py가 처리)
- `/admin update` 시 자동 pip install

---

## v2 (2026-03-29 저녁)

v1의 근본적 한계를 해결하기 위한 대규모 구조 개편. 같은 날 밤에 배포.

### 의도

- 단일 DB를 library.db + librarian.db로 분리. 도서관 데이터와 AI 기억/지식은 성격이 다름
- 도구를 `search` 하나로 통합. v1에서 도구가 너무 많아 AI가 혼란스러워했음
- 비트코인 관련 지식 베이스(`knowledge/*.txt`)를 내장해서 AI 자체 지식에만 의존하지 않게
- 프롬프트를 `persona.txt` + `prompt.txt` + `reminder.txt`로 분리, 샌드위치 구조로 캐릭터 이탈 방지
- 기억 시스템 단순화: 유저별/공통, 단기/장기 구분 제거 → `learned` 테이블 하나로 통합
- 반복 답변 방지 3단계 시스템 도입 (기본 → 맥락 제거 재시도 → 웹 검색 폴백)
- Google Search grounding으로 웹 검색 기능 추가
- API 키 5개 로테이션으로 무료 한도 분산

### 주요 변경

- 모델: gemini-2.5-flash → gemini-2.5-flash-lite (비용 절감)
- DB: database.py (단일) → library_db.py + librarian_db.py (분리)
- 도구: 6개 (search_entries, list_entries, get_entry_detail, send_file, recall_memories, save_memory) → 9개로 정리 (search 통합 + web_search, add_knowledge, add_entry_alias, add_alias 추가)
- 지식 베이스: 0건 → 120건 (10개 txt 파일)
- 별칭 양방향 확장 검색

---

## v1 (2026-03-29 오후)

최초 배포. 비트쨩의 탄생.

### 의도

- 디스코드에서 비트코인 관련 자료(epub)를 공유하고, AI 사서가 자연어로 안내해주는 시스템
- 라이브러리 봇(슬래시 커맨드)과 AI 사서봇(멘션 대화)을 분리해서 역할별 독립 운영
- 비트코인 맥시멀리스트 캐릭터(비트쨩)로 커뮤니티 분위기에 맞는 사서
- startup.py로 두 봇을 동시 관리, `/admin update`로 원격 배포

### 구성

- 모델: gemini-2.5-flash (단일 API 키)
- DB: 단일 database.py + librarian_bot.db
- 도구 4개: list_entries, search_entries, get_entry_detail, send_file
- 페르소나: persona.json에 성격 + 시스템 프롬프트 하드코딩
- 기억: 유저별/공통 구분, 단기/장기 분리
- 반복 방지: 없음
- 웹 검색: 없음
- 지식 베이스: 없음
