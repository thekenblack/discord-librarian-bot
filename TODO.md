# TODO

- 가짜 전달 방지 ("건네줄게" 자연어만 쓰고 deliver 안 부르는 케이스)
- deliver 횟수 제한 (1요청당 최대 N회)
- mood 상대값(+/-) 프롬프트 안내 (character.txt 수정)

## DONE

- id 네이밍 정리 (entry_id, file_id 표기)
- 환율 정보 (업비트 KRW 시세, USD/KRW, 김치 프리미엄)
- 날씨 정보 (8개 도시, Open-Meteo + 국제 온디맨드)
- 뉴스 헤드라인 (국내/국제, search로 조회)
- 타 유저 멘션 / 채널 링크 / 커스텀 이모지
- 미디어 첨부 기능 (attach, librarian/media/ 저장)
- 리트라이 구조 개선 (반복일 때만 재시도, 임계값 0.9)
- 웹링크 인식 (recognize_link, Gemini URL 직접 전달)
- 도서 내용 학습 (book_knowledge, Gemini 요약, status 관리)
- INVALID_ARGUMENT 대응 (히스토리 초기화 + 클린 재시도)
- 히스토리 채널별 → 유저별, 채널 락 → 유저 락
- MAX_HISTORY 20 → 10
- _call_gemini 비동기화 (run_in_executor 내장)
- deliver/mood 인라인 함수 파싱 (positional args 포함)
- mood 1회 적용 + 상대값(+/-) 지원
- [mood:XX] 태그 노출 수정 (인라인 함수 재응답에서도 제거)
- 어드민 알림 대기열 (10초 내 모아서 1회 전송)
- bot.log [수신] 시점 로그 추가
- DM 로그 한글 깨짐 수정 (UTF-8 BOM)
- 직전 대화에 첨부/임베드/링크 정보 포함
- discord.py _ready 충돌 수정 (_bot_ready)
- typing Forbidden/503 에러 처리
- 반복 감지 임계값 0.8 → 0.9

## DROP

- 오디오/비디오 인식 (토큰 비용 대비 실용성 낮음)
