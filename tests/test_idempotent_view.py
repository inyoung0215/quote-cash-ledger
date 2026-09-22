"""중복 열람 이벤트가 와도 과금은 한 번.

최소 한 번 전달이 전제인 이벤트 파이프라인에서는 같은 열람 이벤트가
두 번 도착할 수 있다. 소비하는 쪽이 멱등해야 한다.
"""

from decimal import Decimal

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import CashLedger
from app.services import cash
from tests.conftest import gather_isolated

QUOTE = "q-dup-view"


async def test_concurrent_views_capture_exactly_once(account):
    pro_id = await account()
    async with SessionLocal() as s, s.begin():
        await cash.hold(s, pro_id=pro_id, quote_id=QUOTE, request_id="r1", amount=Decimal("500"))

    # 같은 견적에 열람 이벤트 10개가 동시에 도착한다.
    results = await gather_isolated(
        lambda session, _i: cash.capture(session, quote_id=QUOTE), 10
    )

    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 10, "중복 열람은 오류가 아니라 같은 결과를 돌려줘야 한다(멱등)"

    async with SessionLocal() as s:
        # 원장에 CAPTURE 는 정확히 한 줄.
        n = await s.execute(
            select(func.count())
            .select_from(CashLedger)
            .where(CashLedger.quote_id == QUOTE, CashLedger.entry_type == "CAPTURE")
        )
        assert n.scalar_one() == 1, "열람이 10번 와도 과금 원장은 1건이어야 한다"

        acct = await cash.get_account(s, pro_id)
        assert acct.bonus_cash == Decimal("2500.00"), "차감도 정확히 한 번"
        assert acct.held_bonus == Decimal("0.00")
