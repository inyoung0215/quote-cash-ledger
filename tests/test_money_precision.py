"""금액은 float으로 다루지 않는다."""

from decimal import Decimal

from app.db import SessionLocal
from app.schemas import SendQuoteRequest
from app.services import cash


def test_float_cannot_represent_money():
    assert 0.1 + 0.2 != 0.3
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")


def test_schema_converts_via_string_not_float():
    """Decimal(0.1) 이 아니라 Decimal('0.1').

    Java의 new BigDecimal(0.1) 과 똑같은 함정이다.
    float을 그대로 넘기면 이미 오차가 생긴 값이 들어온다.
    """
    assert Decimal(0.1) != Decimal("0.1")  # noqa: RUF032 - 함정을 보이는 게 목적
    req = SendQuoteRequest(pro_id=1, quote_id="q", request_id="r", amount=0.1)
    assert req.amount == Decimal("0.1")


async def test_many_small_charges_do_not_drift(account):
    """작은 금액 300건을 확정해도 원장 합계가 정확히 맞아야 한다."""
    pro_id = await account(paid="1000.00", bonus="0.00")

    for i in range(300):
        async with SessionLocal() as s, s.begin():
            await cash.hold(
                s, pro_id=pro_id, quote_id=f"q-cent-{i}", request_id="r", amount=Decimal("0.01")
            )
        async with SessionLocal() as s, s.begin():
            await cash.capture(s, quote_id=f"q-cent-{i}")

    async with SessionLocal() as s:
        acct = await cash.get_account(s, pro_id)
        assert acct.paid_cash == Decimal("997.00"), "0.01 * 300 = 3.00 이 정확히 빠져야 한다"
        assert acct.held_paid == Decimal("0.00")
