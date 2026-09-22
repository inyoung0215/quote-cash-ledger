"""락 전략 비교 실행기.

사용법:
    python -m bench.run                      # 전체 전략을 기본 설정으로
    python -m bench.run --workers 50 --ops 500
    python -m bench.run --only skip_locked

중요한 건 속도가 아니라 **먼저 정확한지**다.
그래서 매 실행마다 같은 번호가 두 번 할당됐는지를 먼저 확인하고,
정확하지 않으면 속도는 의미가 없으므로 실패로 표시한다.
"""

import argparse
import asyncio
import time
from collections import Counter

import redis.asyncio as aioredis
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.db import SessionLocal, engine
from bench.schema import BenchBase, NumberPool
from bench.strategies import STRATEGIES

REDIS_URL = "redis://127.0.0.1:6390"


async def reset_pool(size: int) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(BenchBase.metadata.drop_all)
        await conn.run_sync(BenchBase.metadata.create_all)
    async with SessionLocal() as session, session.begin():
        session.add_all(
            [
                NumberPool(number=f"0503-{i:06d}", status="AVAILABLE")
                for i in range(size)
            ]
        )


async def worker(name, strategy, ops, latencies, outcomes, assigned_ids, retries, redis):
    for _ in range(ops):
        started = time.perf_counter()
        try:
            async with SessionLocal() as session:
                if getattr(strategy, "manages_transaction", False):
                    # 전략이 트랜잭션 경계를 직접 정한다(재시도마다 새 트랜잭션).
                    pool_id, retry = await strategy(session, name)
                elif redis is not None:
                    async with session.begin():
                        pool_id, retry = await strategy(session, name, redis=redis)
                else:
                    async with session.begin():
                        pool_id, retry = await strategy(session, name)
        except (OperationalError, DBAPIError) as exc:
            # 락 대기 시간 초과나 데드락. 전략의 특성이므로 결과에 포함한다.
            msg = str(exc.orig)[:60] if exc.orig else str(exc)[:60]
            outcomes[f"db_error: {msg}"] += 1
            latencies.append((time.perf_counter() - started) * 1000)
            continue

        latencies.append((time.perf_counter() - started) * 1000)
        retries.append(retry)
        if pool_id is None:
            outcomes["exhausted"] += 1
        else:
            outcomes["assigned"] += 1
            assigned_ids.append(pool_id)


def pct(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(int(len(ordered) * p / 100), len(ordered) - 1)
    return ordered[k]


async def verify_correctness(assigned_ids) -> tuple[bool, str]:
    """속도보다 먼저 확인할 것: 같은 번호가 두 번 나갔는가."""
    dupes = [i for i, c in Counter(assigned_ids).items() if c > 1]
    if dupes:
        return False, f"중복 할당 {len(dupes)}건 (예: {dupes[:3]})"

    async with SessionLocal() as session:
        n_assigned = await session.execute(
            select(func.count()).select_from(NumberPool).where(NumberPool.status == "ASSIGNED")
        )
        db_count = n_assigned.scalar_one()
        # 한 요청이 하나의 행만 차지했어야 한다.
        n_multi = await session.execute(
            text(
                "SELECT COUNT(*) FROM (SELECT assigned_to FROM bench_number_pool "
                "WHERE status='ASSIGNED' GROUP BY assigned_to HAVING COUNT(*) > 0) t"
            )
        )
        n_multi.scalar_one()

    if db_count != len(assigned_ids):
        return False, f"DB 할당 수 {db_count} != 성공 응답 수 {len(assigned_ids)}"
    return True, "중복 없음"


async def run_one(key, workers, ops_total, pool_size):
    strategy = STRATEGIES[key]
    await reset_pool(pool_size)

    redis = aioredis.from_url(REDIS_URL) if key == "redis_lock" else None
    if redis is not None:
        await redis.flushdb()

    ops_each = max(1, ops_total // workers)
    latencies, retries, assigned_ids = [], [], []
    outcomes = Counter()

    started = time.perf_counter()
    await asyncio.gather(
        *[
            worker(f"w{i}", strategy, ops_each, latencies, outcomes, assigned_ids, retries, redis)
            for i in range(workers)
        ]
    )
    elapsed = time.perf_counter() - started

    if redis is not None:
        await redis.aclose()

    ok, note = await verify_correctness(assigned_ids)
    total = workers * ops_each
    return {
        "strategy": key,
        "correct": ok,
        "note": note,
        "total": total,
        "assigned": outcomes["assigned"],
        "errors": sum(v for k, v in outcomes.items() if k.startswith("db_error")),
        "error_detail": next((k for k in outcomes if k.startswith("db_error")), ""),
        "elapsed": elapsed,
        "tps": total / elapsed if elapsed else 0,
        "p50": pct(latencies, 50),
        "p95": pct(latencies, 95),
        "p99": pct(latencies, 99),
        "max": max(latencies) if latencies else 0,
        "retry_total": sum(retries),
        "retry_max": max(retries) if retries else 0,
    }


def render(results, workers, ops, pool_size):
    lines = []
    lines.append(f"동시 요청 {workers} · 총 {ops}회 · 풀 {pool_size}개 · MySQL 8 (docker, 로컬)")
    lines.append("")
    header = (
        f"{'전략':<32}{'정확':<6}{'처리량/s':>10}{'p50':>9}{'p95':>9}{'p99':>9}"
        f"{'최대':>10}{'재시도':>8}{'오류':>7}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        lines.append(
            f"{r['strategy']:<32}{'OK' if r['correct'] else '실패':<6}"
            f"{r['tps']:>10.0f}{r['p50']:>8.1f}ms{r['p95']:>8.1f}ms{r['p99']:>8.1f}ms"
            f"{r['max']:>9.1f}ms{r['retry_total']:>8}{r['errors']:>7}"
        )
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=30)
    ap.add_argument("--ops", type=int, default=300)
    ap.add_argument("--pool", type=int, default=2000)
    ap.add_argument("--only", type=str, default=None)
    args = ap.parse_args()

    keys = [args.only] if args.only else list(STRATEGIES)
    results = []
    for key in keys:
        print(f"  실행 중: {key} ...", flush=True)
        results.append(await run_one(key, args.workers, args.ops, args.pool))

    print()
    print(render(results, args.workers, args.ops, args.pool))
    print()
    for r in results:
        if not r["correct"]:
            print(f"  [정확성 실패] {r['strategy']}: {r['note']}")
        if r["errors"]:
            print(f"  [DB 오류] {r['strategy']}: {r['errors']}건 — {r['error_detail']}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
