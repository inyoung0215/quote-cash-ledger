# 락 전략 비교

사용 가능한 자원 하나를 여러 요청이 동시에 가져가려 할 때,
여섯 가지 방법의 **정확성과 지연**을 같은 조건에서 측정한다.

숨고 기술 블로그의 *'Database Lock으로 인한 Slow Query 제거하기'*(안심번호 할당)에서
다룬 문제를 단순화해 재현했다.

## 실행

```bash
docker compose up -d --wait          # MySQL 8 + Redis
python -m bench.run                  # 기본: 동시 30, 총 600회, 풀 3000
python -m bench.run --workers 50 --ops 1000
python -m bench.run --only skip_locked
```

## 측정하는 것

**속도보다 정확성을 먼저 본다.** 매 실행마다 같은 번호가 두 번 할당됐는지 확인하고,
정확하지 않으면 속도는 의미가 없으므로 실패로 표시한다.

그다음 평균이 아니라 **p95 · p99**를 본다. 평균은 대기가 쌓이는 걸 숨긴다.

| 전략 | |
|---|---|
| `for_update` | 맨 앞 행을 잠근다. 확실하지만 대기가 쌓인다 |
| `skip_locked` | 잠긴 행은 건너뛴다 (MySQL 8+) |
| `conditional_update` | 잠그지 않고 상태 조건으로 선점. 실패하면 재시도 |
| `conditional_update_spread` | 위 + 후보를 분산해서 고른다 |
| `conditional_update_spread_tx` | 위 + **재시도를 트랜잭션 밖으로** |
| `redis_lock` | Redis SETNX 로 후보 행을 잠근다 |

## 결과

→ **[RESULTS.md](RESULTS.md)**

요약하면, `SKIP LOCKED` 가 이 문제에 가장 맞았고(처리량 6배, p99 14배 낮음),
조건부 UPDATE 는 **후보 분산**과 **재시도의 트랜잭션 경계**에 따라 p99 가
1,727ms 에서 123ms 까지 움직였다. 경계를 잘못 두면 데드락도 난다.
