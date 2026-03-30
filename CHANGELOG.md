# Changelog

배포 당시 의도를 기록한다. 배포 후 실제 동작은 FEEDBACKS.md에 기록.

---

## v3 (2026-03-30 ~)

프로젝트 구조 전면 개편 + AI 판단 위임. v2 로그 분석을 기반으로 강제 트리거/필터를 전부 제거하고 모델에게 맡김.

### 의도

**구조:**
- 플랫 구조 → `library/` + `librarian/` 패키지 분리
- `config.json` (커밋) + `.env` (비밀값) 설정 분리
- `startup.py`를 config.json 기반으로 재작성, 이후 불변
- 런타임 데이터 격리: `data/`, `files/`, `logs/`
- `migrations/` + `patches/` 시스템

**AI 단순화 (v2 로그 분석 기반):**
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

## v2 (f477477, 2026-03-29)

v1의 근본적 한계를 해결하기 위한 대규모 구조 개편. 같은 날 밤에 배포.

### 의도

- 단일 DB를 library.db + librarian.db로 분리
- 도구를 `search` 하나로 통합
- 비트코인 관련 지식 베이스 내장 (knowledge/*.txt, 120건)
- 프롬프트 분리 (persona.txt + prompt.txt + reminder.txt), 샌드위치 구조
- 기억 시스템 단순화 (learned 테이블 하나)
- 반복 방지 3단계 + Google Search grounding + API 키 5개 로테이션

### 주요 변경

- 모델: gemini-2.5-flash → gemini-2.5-flash-lite
- DB 분리, 도구 통합, 지식 베이스 120건, 별칭 양방향 확장

---

## v1 (ce1da2e, 2026-03-29)

최초 배포. 비트쨩의 탄생.

### 의도

- 디스코드에서 비트코인 관련 자료를 공유하고 AI 사서가 안내하는 시스템
- 라이브러리 봇 + AI 사서봇 분리, startup.py로 동시 관리

### 구성

- 모델: gemini-2.5-flash, 단일 API 키, 단일 DB
- 도구 4개, 페르소나 하드코딩, 반복 방지/웹 검색/지식 베이스 없음
