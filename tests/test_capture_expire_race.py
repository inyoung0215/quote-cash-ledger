"""이 레포에서 가장 중요한 테스트.

열람과 만료가 경계 시각에 동시에 일어나면 어떻게 되는가.
둘 다 성공하면 고수는 과금당하고 환급도 받는다(회사 손해).
둘 다 실패하면 보류가 영원히 남는다(고수 손해).
정확히 하나만 성공해야 한다.

두 처리 모두 'status = HOLD 일 때만' 바꾸는 조건부 UPDATE이므로
먼저 선점한 쪽만 rowcount 1을 받고, 나머지는 0으로 물러난다.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.clock import utcnow
from app.db import SessionLocal
from app.errors import ChargeAlreadyReleased
from app.models import CashLedger, QuoteCharge
from app.services import cash
from tests.conftest import gather_isolated


async def _hold_expiring_now(pro_id: int, quote_id: str) -> None:
    """이미 만료 시각이 지난 보류를 하나 만든다."""
    async with SessionLocal() as s, s.begin():
        charge = await cash.hold(
            s, pro_id=pro_id, quote_id=quote_id, request_id="r1", amount=Decimal("500")
        )
        charge.hold_expires_at = utcnow() - timedelta(seconds=1)


@pytest.mark.parametrize("round_no", range(15))
async def test_view_and_expire_race_exactly_one_wins(account, round_no):
    """경쟁은 타이밍에 따라 결과가 갈리므로 여러 번 돌린다."""
    quote_id = f"q-race-{round_no}"
    pro_id = await account()
    await _hold_expiring_now(pro_id, quote_id)

    # asyncio.gather 는 넘긴 순서대로 태스크를 깨운다.
    # 항상 같은 순서로 두면 한쪽이 늘 먼저 이겨서, 반대 순서는 한 번도 재현되지 않는다.
    # 라운드마다 순서를 바꿔 양쪽을 모두 밟는다.
    roles = ("capture", "release") if round_no % 2 == 0 else ("release", "capture")

    async def contender(session, i):
        if roles[i] == "capture":
            return ("capture", await cash.capture(session, quote_id=quote_id))
        return ("release", await cash.release(session, quote_id=quote_id))

    results = await gather_isolated(contender, 2)

    captured = any(
        isinstance(r, tuple) and r[0] == "capture" and r[1].status == "CAPTURED"
        for r in results
    )
    released = any(
        isinstance(r, tuple) and r[0] == "release" and r[1] is not None for r in results
    )
    capture_rejected = any(isinstance(r, ChargeAlreadyReleased) for r in results)

    # 정확히 한쪽만 이긴다.
    assert captured != released, f"둘 다 성공하거나 둘 다 실패했다: {results}"
    if released:
        assert capture_rejected, "해제가 이겼다면 열람은 409로 거절돼야 한다"

    async with SessionLocal() as s:
        final = await s.execute(
            select(QuoteCharge.status).where(QuoteCharge.quote_id == quote_id)
        )
        status = final.scalar_one()
        assert status in ("CAPTURED", "RELEASED")

        # 원장에는 확정과 해제 중 하나만 남는다. HOLD 한 줄 + 결과 한 줄 = 2줄.
        n = await s.execute(
            select(func.count()).select_from(CashLedger).where(CashLedger.quote_id == quote_id)
        )
        assert n.scalar_one() == 2, "보류 1건 + 확정 또는 해제 1건이어야 한다"

        await cash.assert_ledger_matches_account(s, pro_id)


async def test_release_is_safe_to_run_twice(account):
    """만료 배치가 두 번 돌아도 해제는 한 번만 일어난다."""
    quote_id = "q-release-twice"
    pro_id = await account()
    await _hold_expiring_now(pro_id, quote_id)

    async with SessionLocal() as s, s.begin():
        first = await cash.release(s, quote_id=quote_id)
    async with SessionLocal() as s, s.begin():
        second = await cash.release(s, quote_id=quote_id)

    assert first is not None
    assert second is None, "이미 해제된 건은 조건부 UPDATE가 0행을 반환해 건너뛴다"

    async with SessionLocal() as s:
        n = await s.execute(
            select(func.count())
            .select_from(CashLedger)
            .where(CashLedger.quote_id == quote_id, CashLedger.entry_type == "RELEASE")
        )
        assert n.scalar_one() == 1


async def test_hold_not_yet_expired_is_not_released(account):
    """만료 시각 전에는 해제되지 않는다. 조건에 시각까지 넣은 이유."""
    quote_id = "q-not-expired"
    pro_id = await account()
    async with SessionLocal() as s, s.begin():
        await cash.hold(
            s, pro_id=pro_id, quote_id=quote_id, request_id="r1", amount=Decimal("500")
        )

    async with SessionLocal() as s, s.begin():
        assert await cash.release(s, quote_id=quote_id) is None
