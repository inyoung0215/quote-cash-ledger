"""사용 가능한 자원 하나를 동시에 가져가는 네 가지 방법.

모두 같은 계약을 따른다.
    acquire(session, requester) -> (assigned_id | None, retry_count)

None 은 '가져갈 게 없다'는 뜻이다. 예외는 호출자가 실패로 집계한다.
"""

import random

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from bench.schema import NumberPool

MAX_RETRY = 20
# 조건부 UPDATE에서 후보를 분산해 고를 때 몇 개 중에서 고를지
SPREAD_CANDIDATES = 20


async def _mark_assigned(session: AsyncSession, pool_id: int, requester: str) -> None:
    await session.execute(
        update(NumberPool)
        .where(NumberPool.id == pool_id)
        .values(status="ASSIGNED", assigned_to=requester, assigned_at=utcnow())
        .execution_options(synchronize_session=False)
    )


async def for_update(session: AsyncSession, requester: str) -> tuple[int | None, int]:
    """SELECT ... FOR UPDATE

    맨 앞의 사용 가능한 행을 잠그고 가져간다.
    모든 요청이 같은 행을 노리므로 뒤에 온 요청은 앞 트랜잭션이 커밋할 때까지 기다린다.
    확실하지만 대기가 쌓인다. 이게 Slow Query 로 보이는 지점이다.
    """
    row = await session.execute(
        select(NumberPool.id)
        .where(NumberPool.status == "AVAILABLE")
        .order_by(NumberPool.id)
        .limit(1)
        .with_for_update()
    )
    pool_id = row.scalar_one_or_none()
    if pool_id is None:
        return None, 0
    await _mark_assigned(session, pool_id, requester)
    return pool_id, 0


async def skip_locked(session: AsyncSession, requester: str) -> tuple[int | None, int]:
    """SELECT ... FOR UPDATE SKIP LOCKED  (MySQL 8+)

    이미 잠긴 행은 건너뛰고 다음 행을 가져간다.
    '아무거나 하나면 된다'는 할당 문제에 가장 자연스럽게 맞는다. 대기가 없다.
    """
    row = await session.execute(
        select(NumberPool.id)
        .where(NumberPool.status == "AVAILABLE")
        .order_by(NumberPool.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    pool_id = row.scalar_one_or_none()
    if pool_id is None:
        return None, 0
    await _mark_assigned(session, pool_id, requester)
    return pool_id, 0


async def conditional_update(session: AsyncSession, requester: str) -> tuple[int | None, int]:
    """조건부 UPDATE (WHERE status = 'AVAILABLE')

    잠그지 않고 후보를 읽은 뒤, 상태가 그대로일 때만 바꾼다.
    영향 행이 0이면 다른 요청이 선점한 것이므로 다시 고른다.

    외부 의존이 없고 대기도 없지만, **모든 요청이 같은 첫 번째 행을 집기 때문에**
    경합이 반복된다. 그게 재시도 횟수로 드러난다.
    """
    retries = 0
    for _ in range(MAX_RETRY):
        row = await session.execute(
            select(NumberPool.id)
            .where(NumberPool.status == "AVAILABLE")
            .order_by(NumberPool.id)
            .limit(1)
        )
        pool_id = row.scalar_one_or_none()
        if pool_id is None:
            return None, retries

        result = await session.execute(
            update(NumberPool)
            .where(NumberPool.id == pool_id, NumberPool.status == "AVAILABLE")
            .values(status="ASSIGNED", assigned_to=requester, assigned_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            return pool_id, retries
        retries += 1
    return None, retries


async def conditional_update_spread(
    session: AsyncSession, requester: str
) -> tuple[int | None, int]:
    """조건부 UPDATE + 후보 분산

    위와 같지만 맨 앞 하나가 아니라 앞쪽 후보 여러 개 중에서 무작위로 고른다.
    경합의 원인이 '락'이 아니라 '모두가 같은 행을 고른다'는 데 있었다면
    이것만으로 재시도가 줄어야 한다. 그 가설을 확인하는 항목이다.
    """
    retries = 0
    for _ in range(MAX_RETRY):
        rows = await session.execute(
            select(NumberPool.id)
            .where(NumberPool.status == "AVAILABLE")
            .order_by(NumberPool.id)
            .limit(SPREAD_CANDIDATES)
        )
        candidates = list(rows.scalars())
        if not candidates:
            return None, retries
        pool_id = random.choice(candidates)

        result = await session.execute(
            update(NumberPool)
            .where(NumberPool.id == pool_id, NumberPool.status == "AVAILABLE")
            .values(status="ASSIGNED", assigned_to=requester, assigned_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            return pool_id, retries
        retries += 1
    return None, retries


async def redis_lock(
    session: AsyncSession, requester: str, *, redis=None
) -> tuple[int | None, int]:
    """Redis SETNX 락

    후보를 고른 뒤 그 행에 대한 락을 Redis 에서 잡고 처리한다.
    빠르지만 **방어가 DB 밖에 있다.** Redis 가 흔들리면 방어도 함께 사라진다.
    그래서 이 방법을 쓰더라도 최종 방어선은 DB 제약이어야 한다.
    """
    retries = 0
    for _ in range(MAX_RETRY):
        row = await session.execute(
            select(NumberPool.id)
            .where(NumberPool.status == "AVAILABLE")
            .order_by(NumberPool.id)
            .limit(1)
        )
        pool_id = row.scalar_one_or_none()
        if pool_id is None:
            return None, retries

        key = f"bench:lock:pool:{pool_id}"
        got = await redis.set(key, requester, nx=True, ex=5)
        if not got:
            retries += 1
            continue
        try:
            # 락을 잡았어도 상태는 다시 확인해야 한다.
            # 앞선 요청이 락을 풀고 나간 뒤일 수 있다.
            result = await session.execute(
                update(NumberPool)
                .where(NumberPool.id == pool_id, NumberPool.status == "AVAILABLE")
                .values(status="ASSIGNED", assigned_to=requester, assigned_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                return pool_id, retries
            retries += 1
        finally:
            await redis.delete(key)
    return None, retries


STRATEGIES = {
    "for_update": for_update,
    "skip_locked": skip_locked,
    "conditional_update": conditional_update,
    "conditional_update_spread": conditional_update_spread,
    "redis_lock": redis_lock,
}


async def conditional_update_spread_tx(
    session: AsyncSession, requester: str
) -> tuple[int | None, int]:
    """조건부 UPDATE + 후보 분산 + **재시도를 트랜잭션 밖으로**

    `conditional_update_spread` 를 측정했더니 데드락이 났다.
    재시도 루프가 트랜잭션 안에 있어서, 실패한 시도의 행 락이 그대로 쌓이고 있었다.
    (InnoDB 기록상 한 트랜잭션이 11~12개의 행 락을 쥔 채 서로를 기다렸다.)

    후보를 무작위로 고르니 트랜잭션마다 락을 잡는 순서가 달라졌고,
    순환 대기가 만들어졌다. 데드락 4조건 중 '순환 대기'가 그대로 성립한다.

    여기서는 시도 하나를 트랜잭션 하나로 만든다.
    실패하면 즉시 커밋되어 락이 풀리므로 다음 시도까지 들고 가지 않는다.
    """
    retries = 0
    for _ in range(MAX_RETRY):
        async with session.begin():
            rows = await session.execute(
                select(NumberPool.id)
                .where(NumberPool.status == "AVAILABLE")
                .order_by(NumberPool.id)
                .limit(SPREAD_CANDIDATES)
            )
            candidates = list(rows.scalars())
            if not candidates:
                return None, retries
            pool_id = random.choice(candidates)

            result = await session.execute(
                update(NumberPool)
                .where(NumberPool.id == pool_id, NumberPool.status == "AVAILABLE")
                .values(status="ASSIGNED", assigned_to=requester, assigned_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                return pool_id, retries
        retries += 1
    return None, retries


# 이 전략은 트랜잭션 경계를 스스로 관리한다. 실행기가 밖에서 감싸면 안 된다.
conditional_update_spread_tx.manages_transaction = True

STRATEGIES["conditional_update_spread_tx"] = conditional_update_spread_tx
