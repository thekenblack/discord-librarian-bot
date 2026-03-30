# 설치

## 요구 사항

- Python 3.11+
- Discord 봇 토큰 2개 (라이브러리 봇, AI 사서봇)
- Gemini API 키

## 설치 순서

### 1. 환경 변수

```bash
cp env.example .env
```

`.env`를 열어서 값을 입력한다.

| 변수 | 설명 |
|---|---|
| DISCORD_BOT_TOKEN | 라이브러리 봇 토큰 |
| DISCORD_GUILD_ID | 서버 ID (슬래시 커맨드 즉시 동기화, 비우면 전체 동기화) |
| AI_BOT_TOKEN | AI 사서봇 토큰 |
| AI_NAME | 사서봇 이름 (디스코드 표시명과 맞춰야 함) |
| GEMINI_API_KEY | Gemini API 키 |
| ADMIN_USER_IDS | 관리자 디스코드 ID (쉼표 구분) |
| LIGHTNING_ADDRESS | 라이트닝 후원 주소 (선택) |
| TZ | 시간대 (기본: Asia/Seoul) |

### 2. 실행

```bash
python startup.py
```

startup.py가 알아서 처리하는 것:
- venv 생성 + pip install (requirements.txt)
- DB 마이그레이션
- 패치 적용
- DB 백업
- 봇 프로세스 실행

### 3. 업데이트

디스코드에서 `/admin update` 또는 서버에서:

```bash
git pull
python startup.py
```

## 디렉토리 구조

| 경로 | 설명 | git |
|---|---|---|
| data/ | DB 파일 + 백업 | gitignore |
| files/ | 도서관 업로드 파일 | gitignore |
| librarian/media/ | 사서 미디어 파일 | gitignore |
| logs/ | 날짜별 로그 | gitignore |

서버 이전 시 `data/` + `files/` + `librarian/media/`를 옮기면 된다.
