# TODO

감정 시스템 v2 (feel 도구 기반):
- self_/user_ 축 분리 (DB 테이블 재설계)
- self_mood, self_fatigue (전역)
- user_fondness, user_respect, user_familiarity, user_formality, user_patience (유저별)
- feel(target) 대상 유저 지정 (답글 체인에 보이는 아무 유저)
- 감쇠 로직 (조회 시 계산, 축별 반감기 차등)
- bulk 조회 (답글 체인 유저 IN 쿼리 1회)
- 프롬프트 유저 감정 표시 (체인 참여 유저별)

기타:
- 가짜 전달 방지 ("건네줄게" 자연어만 쓰고 deliver 안 부르는 케이스)
- deliver 횟수 제한 (1요청당 최대 N회)

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
- 어드민 알림 대기열 (10초 내 모아서 1회 전송)
- bot.log [수신] 시점 로그 추가
- DM 로그 한글 깨짐 수정 (UTF-8 BOM)
- 직전 대화에 첨부/임베드/링크 정보 포함
- discord.py _ready 충돌 수정 (_bot_ready)
- typing Forbidden/503 에러 처리
- 반복 감지 임계값 0.8 → 0.9
- feel 도구 + 6축 감정 (DB 영구, MoodSystem 대체)
- feel(response: ignore/short) 의도적 무응답
- v4 캐릭터 조정 (프롬프트 부정형 제거, 따뜻한 톤)

## DROP

- mood 태그 텍스트 방식 (feel 도구로 대체, 폴백만 유지)
- 채널별 히스토리 (유저별로 전환)
- 채널 락 (유저 락으로 전환)
- MoodSystem 메모리 기반 (DB 영구 저장으로 전환)
- 프롬프트 캐시/카탈로그 캐시 (매번 빌드로 단순화)
- 어드민 에러 즉시 DM (대기열로 전환)
- BeautifulSoup 텍스트 추출 (Gemini URL 직접 전달로 전환)
- epub Gemini 직접 전달 (텍스트 추출 후 전달로 전환)
- 원문 청크 저장 방식 도서 학습 (Gemini 요약 방식으로 전환)
- 반복 감지 시 빈 응답도 재시도 (반복만 재시도로 변경)
