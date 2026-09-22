import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.models import Base, ProCashAccount

PRO_ID = 1001


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean():
    async with SessionLocal() as session, session.begin():
        for table in ("cash_ledger", "quote_charge", "idempotency_record", "pro_cash_account"):
            await session.execute(text(f"DELETE FROM {table}"))
    yield


@pytest.fixture
async def account():
    """유상 10,000 + 보너스 3,000 을 가진 고수."""

    async def _make(pro_id=PRO_ID, paid="10000.00", bonus="3000.00"):
        async with SessionLocal() as session, session.begin():
            session.add(
                ProCashAccount(
                    pro_id=pro_id,
                    paid_cash=Decimal(paid),
                    bonus_cash=Decimal(bonus),
                )
            )
        return pro_id

    return _make


async def gather_isolated(coro_factory, n):
    """서로 다른 세션(=서로 다른 커넥션)으로 n개 요청을 동시에 실행한다.

    같은 세션을 공유하면 진짜 동시성이 아니라 순차 실행이 된다.
    실제 경쟁을 재현하려면 커넥션을 분리해야 한다.
    """

    async def one(i):
        async with SessionLocal() as session:
            try:
                async with session.begin():
                    return await coro_factory(session, i)
            except Exception as e:  # noqa: BLE001 - 테스트에서 예외도 결과로 본다
                return e

    return await asyncio.gather(*[one(i) for i in range(n)])
