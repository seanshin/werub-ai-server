# v2.125.45 — `dedup_call` 대기자가 락을 쥔 채 기다리던 직렬화 봉합

기준 v2.125.44 · 출처 = v2.125.44 §5 부수 발견

## 0. 한 줄

같은 키의 중복 요청이 `_inflight_lock`을 **쥔 채** 최대 180s 기다려, 그동안 키가 다른
모든 medical 생성이 락에서 멈추던 것을 — 대기를 락 밖으로 옮겨 풀었다.

## 1. 무엇이 틀려 있었나

`core/llm_resilience.dedup_call`:

```python
with _inflight_lock:
    if key in _inflight:
        return _inflight[key].result(timeout=180)   # ← 락을 쥔 채 대기
```

- 중복 요청 하나가 대기하는 동안 **다른 키**의 `dedup_call` 진입이 전부 락에서 멈춘다.
- 원 요청도 끝난 뒤 `finally`에서 같은 락을 잡아야 키를 지울 수 있다. 결과 설정
  (`set_result`)은 락이 필요 없어 대기자가 깨어나므로 **교착은 아니다** — 직렬화다.
- 호출자는 `routes/medical.py` `_medical_generate` 한 곳. 같은 질문이 겹치는 순간
  (HIS 재시도·더블 클릭) 다른 진료실의 medical 생성이 그 질문이 끝날 때까지 줄을 섰다.

같이 본 것:
- 대기자는 원 요청 실패 시 `None`을 받는데 **로그가 없었다**(반복 클래스 #1의 모양).
  medical은 `None`을 `MedicalGenerationError("dedup wait failed")`로 올리므로 응답은
  정직했지만 원인은 흔적이 없었다.
- 원 요청이 `BaseException`(스레드 중단 등)으로 빠지면 future가 영영 안 닫혀
  대기자가 180s를 채웠다.

## 2. 처치

| 지점 | 처치 |
|---|---|
| 대기 | 락 안에서는 future **조회·등록만**, `result(timeout=180)`은 락 **밖** |
| 대기 실패 | `logger.warning("[dedup] 대기 실패 key=… 예외형: 사유")` — 반환(`None`)은 불변 |
| 원 요청 이탈 | `finally`에서 future가 안 닫혔으면 `RuntimeError("dedup owner aborted")`로 닫음 |
| 정리 | `_inflight[key] is future`일 때만 pop(남의 future를 지우지 않게) |

**계약 불변**: 대기자는 결과를 공유하고 실패면 `None` · 원 요청 예외는 그대로 전파 ·
medical 호출자 무수정.

## 3. 검증

신규 `tests/test_dedup_call_lock.py` 5건(스레드로 실제 재현):

1. **키 A 중복 대기 중 키 B가 1s 안에 끝난다**(핵심)
2. 중복 요청은 실행 1회로 결과 공유
3. 원 요청 예외 전파 + 대기자 `None` + **warning 로그**
4. 원 요청 `BaseException` → 대기자가 즉시 풀림
5. 완료 후 키 해제(진행 중 공유일 뿐 캐시 아님)

🔑 **탐지기부터 검증**: 같은 테스트를 **구 코드에 걸어 3/5 실패**(1·3·4 — 1은 B가 막힘,
4는 180s 대기) · **신 코드 5/5 통과**를 둘 다 실측했다. 테스트가 결함을 실제로 잡는다.

전체 회귀 **2223 passed**·9 skipped·1 xpassed(2218→+5 · GPU 임시 worktree — 라이브 레포 무접촉).
