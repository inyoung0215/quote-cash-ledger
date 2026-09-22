"""스키마 생성 + 데모 데이터.

운영이라면 Alembic으로 마이그레이션을 관리해야 한다.
여기서는 레포를 받자마자 돌려볼 수 있는 쪽을 택했다(ADR-0007).
"""

import asyncio
import sys
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Base, ProCashAccount

DEMO_PROS = [
    (1001, Decimal("50000.00"), Decimal("10000.00")),
    (1002, Decimal("3000.00"), Decimal("0.00")),
]


async def main(reset: bool = False) -> None:
    async with engine.begin() as conn:
        if reset:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session, session.begin():
        for pro_id, paid, bonus in DEMO_PROS:
            exists = await session.execute(
                select(ProCashAccount).where(ProCashAccount.pro_id == pro_id)
            )
            if exists.scalar_one_or_none() is None:
                session.add(
                    ProCashAccount(pro_id=pro_id, paid_cash=paid, bonus_cash=bonus)
                )
    await engine.dispose()
    print("스키마 생성 완료. 데모 고수: 1001(유상 50000 + 보너스 10000), 1002(유상 3000)")


if __name__ == "__main__":
    # --reset 은 테스트로 더럽혀진 데이터를 지우고 데모 상태로 되돌린다.
    asyncio.run(main(reset="--reset" in sys.argv))
