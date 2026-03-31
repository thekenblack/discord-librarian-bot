# TODO

감정 시스템 v2:
- self_/user_ 축 분리 (DB 테이블 재설계)
- self_mood, self_fatigue (전역)
- user_fondness, user_respect, user_familiarity, user_formality, user_patience (유저별)
- feel(target) 대상 유저 지정
- 감쇠 로직 (조회 시 계산, 축별 반감기 차등)
- bulk 조회 (답글 체인 유저 IN 쿼리 1회)
- 프롬프트 유저 감정 표시 (체인 참여 유저별)

기능:
- 가짜 전달 방지 ("건네줄게" 자연어만 쓰고 deliver 안 부르는 케이스)
- deliver 횟수 제한 (1요청당 최대 N회)

## DONE

v4:
- feel 도구 + 6축 감정 (DB 영구, MoodSystem 대체)
- feel(response: ignore/short) 의도적 무응답
- v4 캐릭터 조정 (프롬프트 부정형 제거, 따뜻한 톤)
- 히스토리 채널별 → 유저별, 채널 락 → 유저 락
- MAX_HISTORY 20 → 10
- _call_gemini 비동기화 (run_in_executor 내장)
- INVALID_ARGUMENT 대응 (히스토리 초기화 + 클린 재시도)
- _trim_history function_call/response 쌍 보장
- deliver/mood 인라인 함수 파싱 (positional args, 재시도에도 적용)
- 어드민 알림 대기열 (10초 내 모아서 1회 전송)
- bot.log [수신] 시점 로그 추가
- DM 로그 한글 깨짐 수정 (UTF-8 BOM)
- 직전 대화에 첨부/임베드/링크 정보 포함
- discord.py _ready 충돌 수정 (_bot_ready)
- typing Forbidden/503 에러 처리
- 반복 감지 임계값 0.8 → 0.9
- 도서 내용 학습 (book_knowledge, Gemini 요약, status 관리)
- 웹링크 인식 (recognize_link, Gemini URL 직접 전달)
- 미디어 첨부 기능 (attach, librarian/media/ 저장)
- 미디어/URL 재인식 방지 (캐시 히트)
- 환율 정보 (업비트 KRW 시세, USD/KRW, 김치 프리미엄)
- 날씨 정보 (8개 도시, Open-Meteo + 국제 온디맨드)
- 뉴스 헤드라인 (국내/국제, search로 조회)
- 타 유저 멘션 / 채널 링크 / 커스텀 이모지
- id 네이밍 정리 (entry_id, file_id 표기)
- 리트라이 구조 개선 (반복일 때만 재시도)

## ADJUST

- 감정: 메모리 MoodSystem → DB feel 도구
- 히스토리: 채널별 → 유저별
- 락: 채널 락 → 유저 락
- MAX_HISTORY: 20 → 10
- 반복 감지: 빈 응답도 재시도 → 반복만 재시도
- 반복 임계값: 0.8 → 0.9
- 리트라이: 빈 응답 재시도 → 빈 응답 즉시 에러
- 도서 학습: 원문 청크 → Gemini 요약
- epub: Gemini 직접 전달 → 텍스트 추출 후 전달
- URL 인식: BeautifulSoup → Gemini URL 직접 전달
- 어드민 알림: 즉시 DM → 10초 대기열
- 캐릭터: 까칠+쿨 → 밝고 따뜻
- mood: 텍스트 태그 → feel 도구

## DROP

- 프롬프트 캐시/카탈로그 캐시
- MoodSystem 메모리 기반 클래스
- BeautifulSoup 의존성
- mood 텍스트 태그 방식 (폴백만 유지)

## CANCEL

- 디스코드 메시지 포워드 (답글로 못 달아서 실용성 없음)
