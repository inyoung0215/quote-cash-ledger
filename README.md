# 견적 캐시 원장 (quote-cash-ledger)

고수가 견적을 보낼 때 캐시를 **보류**하고, 고객이 열람하면 **확정**하고,
48시간 안에 열람되지 않으면 **해제**하는 2단계 과금 API.

> 숨고가 고수에게 공개한 안내 정책을 참고해 만든 **학습용 구현**입니다.
> 실제 내부 구조와는 무관하며, 공개된 정책만을 전제로 설계했습니다.

```
견적 발송 ──> [HOLD]  ──고객 열람──> [CAPTURED]   과금 확정
                 │
                 └──48h 미열람──────> [RELEASED]   보류 해제
```

## 이 레포가 다루는 문제

발송 시점에는 과금될지 **아직 모른다**. 결과가 확정되지 않은 거래를 어떻게 기록할 것인가.

그리고 그 과정에서 상태가 갈라진다.

- 열람과 만료가 **경계 시각에 동시에** 일어나면?
- 같은 열람 이벤트가 **두 번** 오면?
- 고수가 견적을 동시에 여러 건 보내 **잔액을 넘기면**?
- 클라이언트가 **타임아웃 후 재시도**하면?

전부 틀리면 고수나 회사 중 한쪽이 손해를 본다.
설계 전에 먼저 정리한 실패 시나리오 목록: [`docs/failures.md`](docs/failures.md)

## 핵심 설계 한 줄

**상태를 바꾸는 모든 처리는 "현재 상태일 때만" 바꾸는 조건부 UPDATE로 한다.**
영향받은 행이 0이면 다른 처리가 먼저 선점한 것이므로 그대로 물러난다.

```sql
-- 확정과 해제가 동시에 와도 한쪽만 성공한다
UPDATE quote_charge SET status='CAPTURED' WHERE quote_id=? AND status='HOLD';
UPDATE quote_charge SET status='RELEASED' WHERE quote_id=? AND status='HOLD'
                                            AND hold_expires_at <= NOW();
```

잠글 대상(행)이 이미 DB에 있으므로 분산락을 쓰지 않았다 → [ADR-0002](docs/adr/0002-conditional-update-over-distributed-lock.md)

## 돌려보기

```bash
docker compose up -d --wait     # MySQL 8
pip install -e ".[dev]"
python -m app.bootstrap --reset # 스키마 + 데모 고수 2명 (--reset 은 기존 데이터 삭제)
pytest -v                       # 동시성 재현 테스트 포함 27개
uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
```

동작 확인:

```bash
# 견적 발송 (멱등 키 필수)
curl -X POST localhost:8000/v1/quotes -H 'Idempotency-Key: k1' \
  -H 'Content-Type: application/json' \
  -d '{"pro_id":1001,"quote_id":"q1","request_id":"r1","amount":"1500"}'

# 같은 키로 한 번 더 → 새 견적이 생기지 않고 같은 응답 (Idempotent-Replay: true)
curl -X POST localhost:8000/v1/quotes -H 'Idempotency-Key: k1' \
  -H 'Content-Type: application/json' \
  -d '{"pro_id":1001,"quote_id":"q1","request_id":"r1","amount":"1500"}' -i

curl localhost:8000/v1/pros/1001/cash      # 잔액은 그대로, 사용 가능 잔액만 감소
curl -X POST localhost:8000/v1/quotes/q1/view   # 열람 → 여기서 실제 차감
curl localhost:8000/v1/pros/1001/ledger    # 보류·확정이 각각 한 줄씩
```

## 테스트

27개. 이 중 동시성 재현 테스트가 핵심이다.

| 테스트 | 무엇을 막는가 |
|---|---|
| `test_capture_expire_race` | 열람과 만료의 경계 경쟁. **정확히 한쪽만** 성공 (양쪽 순서 모두 재현) |
| `test_idempotent_view` | 열람 이벤트 10개 동시 도착 → 과금 원장 1건 |
| `test_concurrent_balance` | 잔액 1,000에 400짜리 10건 동시 → **정확히 2건만** 성공 |
| `test_release_is_safe_to_run_twice` | 만료 워커가 두 번 돌아도 해제는 한 번 |
| `test_money_precision` | 0.01 × 300건 확정 후 정확히 3.00 차감 |
| `test_idempotency_key` | 같은 키 재요청 시 견적이 두 건 생기지 않음 |

각 테스트는 `assert_ledger_matches_account` 로 **원장과 잔액이 맞는지**까지 확인한다.

### 테스트가 진짜 검증하는지 확인했다

통과하는 테스트는 증거가 아니다. 구현을 일부러 깨뜨려 실제로 실패하는지 확인했고,
그 과정에서 **경쟁 테스트 하나가 검증하는 척만 하고 있었다는 걸 발견했다.**

→ 전체 기록: [`docs/verification-log.md`](docs/verification-log.md)

## 설계 결정 (ADR)

| | |
|---|---|
| [0001](docs/adr/0001-hold-model-vs-refund-model.md) | 보류 모델 vs 차감-환급 모델 — 왜 보류를 골랐나 |
| [0002](docs/adr/0002-conditional-update-over-distributed-lock.md) | 분산락이 아니라 조건부 UPDATE — 잠글 대상이 이미 있는가 |
| [0003](docs/adr/0003-append-only-ledger.md) | 원장은 덮어쓰지 않는다 — 설명할 수 있어야 한다 |
| [0004](docs/adr/0004-idempotency-key.md) | 멱등 키를 필수로 — 가장 위험한 건 "됐는지 모르는 상태" |
| [0005](docs/adr/0005-decimal-money.md) | 금액은 Decimal 로만 |
| [0006](docs/adr/0006-async-driver.md) | async 엔드포인트에는 비동기 드라이버 |
| [0007](docs/adr/0007-no-alembic.md) | Alembic 대신 create_all — 그리고 이게 운영에선 틀린 이유 |
| [0008](docs/adr/0008-utc-everywhere.md) | 시각은 전부 UTC — 섞이면 사후에 구분할 수 없다 |

## AI 사용

코딩 에이전트와 함께 만들었고, **무엇을 맡기고 무엇을 직접 했는지**와
검증 방법을 남겼다 → [`docs/ai-usage.md`](docs/ai-usage.md)

## 구조

```
app/
  models.py              테이블 4개. 주석에 왜 이 컬럼인지
  clock.py               utcnow(). datetime.now() 를 직접 쓰지 않는다
  services/cash.py       보류·확정·해제. 이 파일이 전부다
  services/idempotency.py
  api/                   quotes / pros / admin
  workers/expire_holds.py  만료 해제 워커 (락 없이 안전)
tests/                   27개
docs/
  failures.md            코드보다 먼저 쓴 실패 시나리오
  verification-log.md    테스트를 깨뜨려 본 기록
  ai-usage.md
  adr/                   설계 결정 7건
bench/                   락 전략 비교 (예정)
```

## 하지 않은 것

인증, 원장 대사 배치, 멱등 레코드 만료, 조회 시점 만료 판단, Alembic.
의도적으로 뺐고 이유는 [`docs/failures.md`](docs/failures.md) 마지막 절에 적었다.

## 스택

Python 3.11+ / FastAPI / SQLAlchemy 2.0 (async) / MySQL 8 / pytest
