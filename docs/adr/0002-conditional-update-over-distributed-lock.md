# ADR-0002. 분산락이 아니라 조건부 UPDATE

## 맥락

동시성 문제가 세 군데 있다.

1. 열람과 만료 해제가 경계 시각에 동시에 일어난다
2. 같은 열람 이벤트가 중복으로 도착한다
3. 고수가 견적을 동시에 여러 건 보내 잔액을 넘긴다

## 결정

**전부 조건부 UPDATE로 해결했다. 분산락을 쓰지 않았다.**

```sql
-- 확정: HOLD 일 때만
UPDATE quote_charge SET status='CAPTURED' WHERE quote_id=? AND status='HOLD';

-- 해제: HOLD 이고 만료됐을 때만
UPDATE quote_charge SET status='RELEASED'
 WHERE quote_id=? AND status='HOLD' AND hold_expires_at <= NOW();

-- 보류: 잔액이 충분할 때만
UPDATE pro_cash_account
   SET held_paid = held_paid + ?, held_bonus = held_bonus + ?
 WHERE pro_id = ?
   AND paid_cash  - held_paid  >= ?
   AND bonus_cash - held_bonus >= ?;
```

영향 행이 0이면 다른 처리가 먼저 선점한 것이다. 그대로 물러난다.

## 이유

**잠글 대상이 이미 DB에 있기 때문이다.**

이게 내 판단 기준이다.

- 잠글 행이 **이미 있으면** → DB 원자성을 먼저 검토한다. 여기가 그 경우다.
- 잠글 행이 **아직 없으면** → 분산락이 필요하다.
  (조회 후 없으면 생성하는 구조에서 중복 생성이 나던 문제가 그랬다.
  계좌가 아직 만들어지지 않아 자원 기반 락이 불가능했고, 요청 주체를 락 키로 삼아야 했다.)

부수 효과로 얻은 것:

- **Redis 의존이 없다.** Redis가 흔들릴 때 방어가 함께 사라지지 않는다.
- **락 대기가 없다.** `SELECT ... FOR UPDATE` 였다면 같은 행에 대기가 쌓인다.
  예약이나 과금처럼 사용자가 기다리는 흐름에서는 대기보다 즉시 실패가 낫다.
- **워커에 락이 필요 없다.** 만료 워커가 여러 대 떠도, 두 번 돌아도 안전하다.

## 대가

- `hold()` 는 잔액 분배를 방금 읽은 값으로 계산한다. 동시 요청에 밀리면 WHERE 조건이
  깨져 영향 행 0이 되고, 다시 읽어 재분배한다. 낙관적 재시도(기본 3회)가 필요하다.
- 경합이 아주 심하면 재시도가 늘어난다. 지금 규모에서는 문제가 아니라고 판단했지만,
  경합이 계속 높아지면 계정 행에 대한 접근 자체를 줄이는 쪽(잔액 샤딩 등)을 봐야 한다.

## 마지막 방어선은 애플리케이션이 아니라 DB에 둔다

`UNIQUE(quote_id)`, `UNIQUE(quote_id, entry_type)`.
위의 로직이 전부 뚫려도 원장이 중복으로 쌓이지는 않는다.
