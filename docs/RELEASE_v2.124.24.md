# v2.124.24 — moderation auto-refine NameError 봉합 (라이브 로그 발견)

2026-07-21 | 계기: "다른 부분 필요 작업" 점검 중 라이브 로그 스캔

## 발견

라이브 저널에서 매시간 반복되는 에러 발견:

```
[auto-refine] error: name '_get_mod_db' is not defined
```

`app.py`의 백그라운드 스레드 `_auto_refine_job`(moderation 학습 데이터 자동 정제,
pending 100건 이상 시 raw_collections→refined_samples)이 `_get_mod_db()`를 호출하는데,
그 함수는 `routes/training.py`에만 정의돼 있고 app.py 스코프에 없습니다. **매 실행
NameError로 조용히 실패**하고 있었습니다.

**HR Law admin 가드(v2.124.21)와 정확히 같은 클래스** — 함수를 import 없이 호출해
기능이 애초에 동작한 적이 없고, 해당 기능이 휴면이라 드러나지 않았습니다.

## 영향

- 현재 moderation.db는 **휴면**(최근 수집 2026-07-10·pending 20건 < 100 임계)이라
  실질 영향은 없었습니다.
- 그러나 moderation 데이터가 pending 100건 이상 쌓이면 **자동 정제가 불능**인 잠재 버그.

## fix

`routes.training._get_mod_db`를 함수 상단에서 lazy import(순환 참조 안전). 라이브
검증: import OK · pending 20건 확인(임계 미달이라 정제 트리거 안 됨, 정상).

## 관련 관찰 — 휴면 기능의 숨은 버그 클래스

이번 세션에서 같은 클래스를 3번 만났습니다(HR Law admin 가드 6곳·moderation auto-refine).
공통점은 **휴면 기능이라 NameError가 드러나지 않았다**는 것입니다. 주요 서브시스템
(korean·counseling·meeting·search·orchestrator)은 status 200으로 정상 응답 확인 —
같은 케이스는 아니나, 저활동 상태(counseling 6/2·story 7/4·meeting 7/8)입니다.

교훈: **status 200이 전 경로 정상을 뜻하지 않는다.** 특정 경로가 NameError여도 그
경로가 안 불리면 헬스체크에 안 잡힌다 → 인가·기능 경로는 실제 호출로 검증해야 한다.
