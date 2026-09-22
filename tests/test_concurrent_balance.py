"""동시 요청이 잔액을 초과해 보류를 잡을 수 없어야 한다.

잔액을 읽고 판단한 뒤 쓰는 구조라면, 그 사이에 다른 요청이 끼어들어
둘 다 '충분하다'고 판단하는 순간이 생긴다. 가상계좌 중복 발급과 같은 모양이다.

여기서는 잠글 대상(계정 행)이 이미 DB에 있으므로 분산락 대신
조건부 UPDATE의 WHERE 절에 잔액 조건을 넣어 DB 원자성으로 해결했다.
"""

from decimal import Decimal

from sqlalchemy import func, select

from app.db import SessionLocal
from app.errors import ConcurrentUpdateExhausted, InsufficientCash
from app.models import QuoteCharge
from app.services import cash


async def test_concurrent_holds_cannot_exceed_balance(account):
    # 사용 가능 잔액 1,000. 400짜리 견적 10건이 동시에 들어온다.
    # 성공해야 하는 건수는 정확히 2건.
    pro_id = await account(paid="1000.00", bonus="0.00")

    from tests.conftest import gather_isolated

    results = await gather_isolated(
        lambda session, i: cash.hold(
            session, pro_id=pro_id, quote_id=f"q-over-{i}", request_id="r", amount=Decimal("400")
        ),
        10,
    )

    ok = [r for r in results if isinstance(r, QuoteCharge)]
    rejected = [r for r in results if isinstance(r, (InsufficientCash, ConcurrentUpdateExhausted))]

    assert len(ok) == 2, f"잔액 1000에 400짜리는 2건만 성공해야 한다. 실제 {len(ok)}건"
    assert len(ok) + len(rejected) == 10, f"예상 못 한 예외: {results}"

    async with SessionLocal() as s:
        acct = await cash.get_account(s, pro_id)
        assert acct.held_paid == Decimal("800.00")
        assert acct.available == Decimal("200.00")
        assert acct.available >= 0, "사용 가능 잔액이 음수가 되면 안 된다"

        n = await s.execute(
            select(func.count()).select_from(QuoteCharge).where(QuoteCharge.pro_id == pro_id)
        )
        assert n.scalar_one() == 2

        await cash.assert_ledger_matches_account(s, pro_id)


async def test_insufficient_balance_is_rejected_immediately(account):
    pro_id = await account(paid="100.00", bonus="0.00")
    async with SessionLocal() as s:
        try:
            async with s.begin():
                await cash.hold(
                    s, pro_id=pro_id, quote_id="q-poor", request_id="r", amount=Decimal("500")
                )
            raise AssertionError("잔액 부족인데 통과했다")
        except InsufficientCash as e:
            # 대기시키지 않고 바로 실패를 돌려준다.
            # 고수 입장에서는 기다리는 것보다 즉시 알려주는 편이 낫다.
            assert e.status_code == 409
