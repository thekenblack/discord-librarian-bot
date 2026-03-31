# TODO

감정 시스템 v2:
- self_/user_ 축 분리 (전역 감정과 유저별 감정 구분)
- self_mood, self_fatigue (유저 무관 봇 자체 상태)
- user_fondness, user_respect, user_familiarity, user_formality, user_patience
- feel(target) 대상 유저 지정 (답글 체인에 보이는 아무 유저에게 감정 표현)
- 감쇠 로직 (조회 시 계산, 축별 반감기 차등)
- bulk 조회 (답글 체인 유저 IN 쿼리 1회)
- 프롬프트 유저 감정 표시 (체인 참여 유저별 수치 삽입)

기능:
- 가짜 전달 방지 (deliver 안 부르고 "건네줄게" 텍스트만 쓰는 케이스)
- deliver 횟수 제한 ("다 가져와" 시 8권 연타 방지)

## DONE

v4:
- feel 도구 + 6축 감정 (메모리 → DB 영구 저장)
- feel(response: ignore/short) 의도적 무응답/짧은 응답
- v4 캐릭터 조정 (부정형 프롬프트 제거, 따뜻한 톤으로)
- 히스토리 채널별 → 유저별 (다른 유저 도구 호출 끼어듦 방지)
- MAX_HISTORY 20 → 10 (토큰 절약)
- _call_gemini 비동기화 (이벤트 루프 블로킹 근절)
- INVALID_ARGUMENT 대응 (히스토리 초기화 + 클린 재시도)
- _trim_history function_call/response 쌍 보장 (trim 시 꼬임 방지)
- deliver/mood 인라인 함수 파싱 (재시도 응답에서 텍스트 노출 방지)
- 어드민 알림 대기열 (에러 연타 시 DM 폭탄 방지)
- bot.log [수신] 시점 로그 추가 (처리 시점과 대기 시간 구분)
- DM 로그 한글 깨짐 수정 (UTF-8 BOM)
- 직전 대화에 첨부/임베드/링크 정보 포함 (채널 맥락 누락 방지)
- discord.py _ready 충돌 수정 (bool vs asyncio.Event)
- typing Forbidden/503 에러 처리 (on_message 사망 방지)
- 반복 감지 임계값 0.8 → 0.9 (다른 유저 유사 답변 오탐 방지)
- 도서 내용 학습 (epub/pdf → Gemini 요약 → book_knowledge)
- 웹링크 인식 (recognize_link)
- 미디어 첨부 기능 (attach, librarian/media/ 저장)
- 미디어/URL 재인식 방지 (캐시 히트로 비용 절감)
- 환율 정보 (업비트 KRW 시세, USD/KRW, 김치 프리미엄)
- 날씨 정보 (8개 도시 + 국제 온디맨드)
- 뉴스 헤드라인 (국내/국제, search로 조회)
- 타 유저 멘션 / 채널 링크 / 커스텀 이모지
- id 네이밍 정리 (AI가 file_id와 entry_id 혼동 방지)
- 리트라이 구조 개선 (반복일 때만 재시도, 빈 응답은 즉시 에러)

## ADJUST (계획을 바꿔 조정한 것)

- 감정: 메모리 MoodSystem → DB feel 도구 (재부팅 시 초기화 방지)
- 히스토리: 채널별 → 유저별 (다른 유저 간섭 방지)
- 락: 채널 락 → 유저 락
- MAX_HISTORY: 20 → 10 (매 요청마다 히스토리 전체가 API에 들어감)
- 반복 감지: 빈 응답도 재시도 → 반복만 재시도 (빈 응답은 다른 문제)
- 반복 임계값: 0.8 → 0.9 (다른 유저한테 비슷한 답 시 오탐)
- 도서 학습: 원문 청크 → Gemini 요약 (사서가 내용을 숙지하는 느낌)
- epub: Gemini 직접 전달 → 텍스트 추출 후 전달 (epub MIME 미지원)
- URL 인식: BeautifulSoup → Gemini URL 직접 전달 (동적 페이지/영상도 인식)
- 어드민 알림: 즉시 DM → 10초 대기열 (에러 연타 시 코드블록+파일 폭탄)
- 캐릭터: 까칠+쿨 → 밝고 따뜻 (롱런 가능한 캐릭터로)
- mood: 텍스트 태그 → feel 도구 (태그 노출 방지 + 다축 감정)

## DROP (있었다가 사라진 것)

- 프롬프트 캐시/카탈로그 캐시 (매번 빌드로 단순화)
- MoodSystem 메모리 기반 클래스 (feel DB로 대체)
- BeautifulSoup 의존성 (Gemini가 직접 URL 읽음)
- mood 텍스트 태그 주 방식 (폴백만 유지)

## CANCEL (처음부터 안 하기로 한 것)

- 디스코드 메시지 포워드 (답글로 못 달아서 실용성 없음)
