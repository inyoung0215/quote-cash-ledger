"""정상 흐름: 보류 → 확정. 그리고 보류가 잔액을 깎지 않는다는 것."""

from decimal import Decimal

from app.db import SessionLocal
from app.services import cash

QUOTE = "q-basic-1"


async def test_hold_reserves_but_does_not_deduct(account):
    pro_id = await account()

    async with SessionLocal() as s, s.begin():
        await cash.hold(s, pro_id=pro_id, quote_id=QUOTE, request_id="r1", amount=Decimal("500"))

    async with SessionLocal() as s:
        acct = await cash.get_account(s, pro_id)
        # 잔액 자체는 그대로다. 아직 고객이 열람하지 않았다.
        assert acct.paid_cash == Decimal("10000.00")
        assert acct.bonus_cash == Decimal("3000.00")
        # 사용 가능 잔액만 줄었다.
        assert acct.available == Decimal("12500.00")
        # 보너스를 먼저 쓴다.
        assert acct.held_bonus == Decimal("500.00")
        assert acct.held_paid == Decimal("0.00")


async def test_capture_deducts_once(account):
    pro_id = await account()

    async with SessionLocal() as s, s.begin():
        await cash.hold(s, pro_id=pro_id, quote_id=QUOTE, request_id="r1", amount=Decimal("500"))
    async with SessionLocal() as s, s.begin():
        charge = await cash.capture(s, quote_id=QUOTE)

    assert charge.status == "CAPTURED"
    async with SessionLocal() as s:
        acct = await cash.get_account(s, pro_id)
        assert acct.bonus_cash == Decimal("2500.00")  # 보너스에서 차감
        assert acct.paid_cash == Decimal("10000.00")
        assert acct.held_bonus == Decimal("0.00")     # 보류도 함께 풀림
        assert acct.available == Decimal("12500.00")


async def test_split_spills_over_to_paid_cash(account):
    """보너스가 모자라면 나머지는 유상캐시에서 나간다. 분배를 원장에 남긴다."""
    pro_id = await account(paid="10000.00", bonus="300.00")

    async with SessionLocal() as s, s.begin():
        charge = await cash.hold(
            s, pro_id=pro_id, quote_id=QUOTE, request_id="r1", amount=Decimal("500")
        )
    assert charge.bonus_portion == Decimal("300.00")
    assert charge.paid_portion == Decimal("200.00")
