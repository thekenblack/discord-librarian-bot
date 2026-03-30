# Discord Librarian Bot

비트코인 맥시멀리스트 커뮤니티(시타델)를 위한 디스코드 도서관 봇.

## 구성

**라이브러리 봇** — 슬래시 커맨드로 자료(책장/파일)를 관리.
**AI 사서봇 (비트쨩)** — Gemini 기반 대화형 사서. 멘션하면 반응.

두 봇이 하나의 도서관을 함께 운영. startup.py가 동시 관리.

## 주요 기능

- 자연어로 자료 검색/전송 (function calling)
- 비트코인 관련 지식 내장 (knowledge/*.txt)
- 유저가 가르치면 기억 (learned 테이블)
- 웹 검색으로 최신 정보 제공 + 자체 학습
- 이미지/PDF 인식 (멀티모달)
- 페이지 시스템 (도서 분류/정렬)
- soft delete (hidden/forgotten)

## 설치

```
cp env.example .env    # 토큰, API 키 입력
python startup.py      # venv 자동 생성, 패키지 설치, 봇 시작
```

## 업데이트

서버에서 `/admin update` → git pull → pip install → 전체 재시작.

## 구조

```
library/        라이브러리 봇 (슬래시 커맨드)
librarian/      AI 사서봇 (Gemini, 대화)
data/           DB + 백업 (gitignore)
files/          업로드된 파일 (gitignore)
logs/           날짜별 로그 (gitignore)
migrations/     DB 스키마 마이그레이션
patches/        DB 데이터 패치
```

기술적 상세는 CLAUDE.md, 버전 이력은 CHANGELOG.md, 운영 피드백은 FEEDBACKS.md 참고.
