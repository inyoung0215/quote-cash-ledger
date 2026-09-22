# 검증 루프 기록

테스트가 통과한다는 사실만으로는 그 테스트가 무언가를 검증한다는 증거가 되지 않는다.
그래서 **구현을 일부러 깨뜨려서 테스트가 실제로 실패하는지** 확인했다.
아래는 실행 결과 그대로다.

---

## 변이 1. `capture()` 의 조건부 UPDATE 에서 `status = 'HOLD'` 조건 제거

```python
# 원래
.where(QuoteCharge.quote_id == quote_id, QuoteCharge.status == "HOLD")
# 변이
.where(QuoteCharge.quote_id == quote_id)
```

이렇게 하면 이미 해제된 보류에도 과금이 일어난다. 반드시 잡혀야 한다.

**1차 결과 — 절반만 잡혔다.**

```
FAILED tests/test_idempotent_view.py::test_concurrent_views_capture_exactly_once
1 failed, 17 passed
```

중복 열람 테스트는 잡았는데, **정작 이걸 잡으라고 만든 경쟁 테스트 15라운드가 전부 통과했다.**

원인을 찾아보니 테스트 쪽 문제였다.
`asyncio.gather` 는 넘긴 순서대로 태스크를 깨우기 때문에, 항상 열람을 먼저 넘기면
**열람이 늘 먼저 이기고 "해제가 먼저 이기는 순서"는 한 번도 재현되지 않았다.**
15라운드를 돌렸지만 실제로는 같은 시나리오를 15번 반복한 것이었다.

> 반복 횟수를 늘리는 것과 경우의 수를 늘리는 것은 다르다.
> 라운드 수를 믿고 넘어갔으면 이 테스트는 계속 통과하는 척만 했을 것이다.

**테스트 보강** — 라운드마다 두 처리의 실행 순서를 바꿔 양쪽을 모두 밟게 했다.

```python
roles = ("capture", "release") if round_no % 2 == 0 else ("release", "capture")
```

**2차 결과 — 잡힌다.**

```
FAILED ...test_view_and_expire_race_exactly_one_wins[1]
FAILED ...test_view_and_expire_race_exactly_one_wins[3]
FAILED ...test_view_and_expire_race_exactly_one_wins[5]
FAILED ...test_view_and_expire_race_exactly_one_wins[7]
FAILED ...test_view_and_expire_race_exactly_one_wins[9]
FAILED ...test_view_and_expire_race_exactly_one_wins[11]
FAILED ...test_view_and_expire_race_exactly_one_wins[13]
7 failed, 10 passed
```

실패한 라운드가 전부 홀수 = 해제를 먼저 넘긴 라운드다. 의도한 대로 갈렸다.

---

## 변이 2. `hold()` 의 잔액 조건부 UPDATE 에서 WHERE 잔액 조건 제거

```python
# 변이: 잔액이 충분한지 확인하지 않고 보류를 잡는다
.where(ProCashAccount.pro_id == pro_id)
```

**결과 — 잡힌다.**

```
AssertionError: 잔액 1000에 400짜리는 2건만 성공해야 한다. 실제 10건
FAILED tests/test_concurrent_balance.py::test_concurrent_holds_cannot_exceed_balance
```

동시 요청 10건이 전부 통과해 사용 가능 잔액이 음수가 됐다.
읽고-판단하고-쓰는 사이에 다른 요청이 끼어드는, 가상계좌 중복 발급과 같은 모양이다.

---

## 변이 3. `release()` 에서 만료 시각 조건 제거

```python
# 변이: 만료 시각을 보지 않고 해제한다
.where(QuoteCharge.quote_id == quote_id, QuoteCharge.status == "HOLD")
```

**결과 — 잡힌다.**

```
FAILED tests/test_capture_expire_race.py::test_hold_not_yet_expired_is_not_released
```

아직 48시간이 지나지 않은 보류까지 해제됐다.

---

## 정리

| 변이 | 잡혔나 | 비고 |
|---|---|---|
| capture 상태 조건 제거 | 1차 부분 → **보강 후 확실히** | 테스트 쪽 결함을 발견 |
| hold 잔액 조건 제거 | 잡힘 | |
| release 만료 조건 제거 | 잡힘 | |

세 번 중 한 번은 **테스트가 검증하는 척만 하고 있었다.**
이 비율이 이 작업에서 가장 배운 점이다.
