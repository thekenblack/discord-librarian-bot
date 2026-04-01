# TODO

확인 필요:
- 감정 분산 유지 보정 효과 (75956b6 이후 실제 로그 확인)
- 전처리 시간 개선 효과 (66a3899 이후 활발한 채팅 시 확인)
- 비트코인 공부 강요 패턴 (프롬프트 수정 후 빈도 변화)

## DONE

v4 핫픽스 (4/1 하루):
- feel 메타데이터 유출 방지 (5종 패턴: 괄호/슬래시/function_call/JSON/감정변화)
- _strip_feeling 줄 단위 재구성 + 원본 로그 추가
- reaction 파라미터 분리 (response는 모드 전용, reaction은 이모지 전용)
- 감정 범위 0-10 → 0-100, 시간 감쇠, 분산 유지 보정
- 감정 수치 직접 언급/조작 금지 (프롬프트)
- 도구별 1회 제한 (도구 목록 제거 방식)
- deliver/attach 상호 배타 처리
- 내부 ID 노출 방지 (file_id, media_id 등)
- 멘션만 했을 때 직전 대화 맥락으로 반응
- reply 실패 시 무시 (원본 삭제)
- 전처리 시간 개선 (reply_chain + pre_context)
- 이미지 URL 임베드 → Discord 자동 임베드에 위임
- 커스텀 이모지 서버 검증 (미지원 서버에서 ID 노출 방지)
- 빈 볼드 후처리 제거
- search 풀 키워드 우선 + 서브 키워드 보충
- search 미디어/URL 키워드 감지 시 최근 목록 반환
- 도구 이름 정리 (memorize/forget/memorize_alias/forget_alias)
- [mood:XX] 레거시 폴백 정리

v4 초기:
- feel 도구 + 6축 감정 (DB 영구)
- 도구 루프 로컬 리스트 (INVALID_ARGUMENT 근절)
- 히스토리 채널별 → 유저별 + 유저 락
- _call_gemini 비동기화
- 캐릭터 조정 (밝고 따뜻한 톤)
- 도서 내용 학습 (book_knowledge)
- 환율/날씨/뉴스
- URL 인식 + 미디어 첨부

## ADJUST (계획을 바꿔 조정한 것)

- 감정 범위: 0-10 → 0-100 (분산 확보)
- 감정 축: 6축 → 6축 유지, 이름만 변경 (self_tired → self_energy)
- feel response: 이모지 겸용 → reaction 분리
- 도구 제한: feel만 1회 → 전 도구 1회 (도구 목록 제거 방식)
- 이미지 URL: 직접 임베드 → Discord 자동 임베드에 위임
- 빈 응답: 에러 메시지 → 무응답 → 함수 시도면 리트라이, 아니면 무응답
- 인라인 함수: 실행+재응답 → 실행+남은 텍스트만 사용

## DROP (있었다가 사라진 것)

- MoodSystem 메모리 기반 클래스
- BeautifulSoup 의존성
- mood 텍스트 태그 (feel 도구로 완전 대체)
- 프롬프트 최근 인식 캐시 (토큰 절약)
- 프롬프트 감정 변동 기록
- 프롬프트 날씨 (search로 이동)
- 인라인 함수 재응답 (남은 텍스트만 사용으로 단순화)

## CANCEL (처음부터 안 하기로 한 것)

- 디스코드 메시지 포워드 (답글로 못 달아서 실용성 없음)

## INEFFECTIVE (생각보다 효과적이지 않은 것)

- add_entry_alias 도구 (라이브러리 엔트리 별칭. 사용 실적 없음)
- customs 테이블 (초기 데이터만 있고 이후 추가 없음)
- web_results 테이블 (url_results 분리 후 키워드 검색 캐시 용도만)
- server_log.py (동작은 하지만 server.log 활용 빈도 낮음)
- error.txt (빈 응답 무응답 처리로 거의 안 쓰임)

## IRRELEVANT (완전히 대체되어 불필요한 것)

- mood.py (feel 도구로 완전 대체. import 주석 처리됨)
- add_knowledge 도구 (memorize로 통합됨)
