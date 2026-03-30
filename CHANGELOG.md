# Changelog

## v3.0.0-alpha (2026-03-30)

프로젝트 구조 전면 개편. 서버에서 재시작 시 자동 마이그레이션.

### 구조

- 플랫 구조 → `library/`, `librarian/` 패키지 분리
- `config.json` (커밋) + `.env` (비밀값) 설정 분리
- 런타임 데이터 격리: `data/` (DB), `files/` (파일), `logs/` (로그)
- `startup.py`를 config.json 기반으로 재작성 — 이후 수정 불필요
- `migrations/` 디렉토리 기반 DB 마이그레이션 시스템 도입

### AI

- 모델을 config.json에서 설정 가능하도록 변경 (gemini-2.5-flash-lite → gemini-2.5-flash)
- 구조화된 대화 로그 도입 (`logs/chat.jsonl`) — 버전/모델/도구 호출/에러를 JSON으로 기록
- 시작 시 버전 + git hash + 모델 + 프롬프트 해시 기록

### 네이밍

- `ai/` → `librarian/`
- `ai_bot.log` → `logs/bot.log`
- `uploads/` → `files/`
- `cogs/library.py` → `library/cogs/commands.py`

### 배포

- `/admin update` 시 자동 pip install 추가
- 구 구조에서 신 구조로 파일 자동 마이그레이션

---

## v2 (이전)

플랫 구조. 라이브러리 봇 + AI 사서봇 분리.

- 라이브러리 봇 (`bot.py`) + AI 사서봇 (`ai.py`) 2개 봇 체제
- Gemini 2.5 Flash Lite 기반 function calling
- 9개 도구: search, list_entries, get_entry_detail, send_file, save_memory, add_knowledge, add_entry_alias, add_alias, web_search
- 페르소나: 비트쨩 (비트코인 맥시멀리스트 사서)
- API 키 5개 로테이션, 일일 한도 자동 비활성화
- 별칭 확장 검색, 기억 자동 저장, 반복 방지 3단계
- startup.py 프로세스 매니저 (exit 42 → git pull → 재시작)

---

## v1 (이전)

단일 봇, 단일 DB 구조. 상세 불명.
