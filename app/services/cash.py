"""견적 캐시 2단계 과금의 핵심 로직.

설계 원칙 세 가지:
  1. 발송 시점에 차감하지 않는다. 결과가 아직 확정되지 않았기 때문이다.
  2. 상태를 바꾸는 모든 처리는 '현재 상태일 때만' 바꾸는 조건부 UPDATE로 한다.
     영향받은 행이 0이면 다른 처리가 먼저 선점한 것이므로 그대로 물러난다.
  3. 원장은 덮어쓰지 않는다. 보류/확정/해제를 각각 남긴다.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import utcnow
from app.config import settings
from app.errors import (
    ChargeAlreadyReleased,
    ChargeNotFound,
    ConcurrentUpdateExhausted,
    InsufficientCash,
)
from app.models import CashLedger, ProCashAccount, QuoteCharge


async def get_account(session: AsyncSession, pro_id: int) -> ProCashAccount:
    acct = await session.get(ProCashAccount, pro_id)
    if acct is None:
        raise ChargeNotFound(f"고수 계정을 찾을 수 없습니다: {pro_id}")
    return acct


def _split(available_bonus: Decimal, amount: Decimal) -> tuple[Decimal, Decimal]:
    """재원 분배: 보너스캐시를 먼저 쓴다.

    보너스는 통상 유효기간이 있어 먼저 소진시키는 편이 고수에게 유리하다.
    다만 이건 정책 선택이지 기술 판단이 아니다. 그래서 분배 결과를
    원장에 paid_delta / bonus_delta 로 남겨 사후에 설명할 수 있게 한다(ADR-0001).
    """
    bonus_part = min(max(available_bonus, Decimal("0")), amount)
    paid_part = amount - bonus_part
    return paid_part, bonus_part


async def hold(
    session: AsyncSession,
    *,
    pro_id: int,
    quote_id: str,
    request_id: str,
    amount: Decimal,
    now: datetime | None = None,
) -> QuoteCharge:
    """견적 발송 = 보류(HOLD).

    잔액을 깎지 않고 '보류' 버킷만 늘린다. 고수 입장에서 사용 가능 잔액은 줄지만
    실제 차감은 고객이 열람해야 일어난다.
    """
    now = now or utcnow()
    expires_at = now + timedelta(hours=settings.hold_ttl_hours)

    for _attempt in range(settings.hold_max_retry):
        acct = await get_account(session, pro_id)
        await session.refresh(acct)

        available_bonus = acct.bonus_cash - acct.held_bonus
        available = acct.available
        if available < amount:
            raise InsufficientCash(
                f"사용 가능 잔액이 부족합니다. 필요 {amount}, 가능 {available}"
            )

        paid_part, bonus_part = _split(available_bonus, amount)

        # 핵심: 분배는 방금 읽은 값으로 계산했지만, 실제 반영은 조건부 UPDATE로 한다.
        # 그 사이 다른 요청이 보류를 늘렸다면 WHERE 조건이 깨져 rowcount가 0이 되고,
        # 잔액을 초과해 보류가 잡히는 일이 생기지 않는다.
        stmt = (
            update(ProCashAccount)
            .where(
                ProCashAccount.pro_id == pro_id,
                ProCashAccount.paid_cash - ProCashAccount.held_paid >= paid_part,
                ProCashAccount.bonus_cash - ProCashAccount.held_bonus >= bonus_part,
            )
            .values(
                held_paid=ProCashAccount.held_paid + paid_part,
                held_bonus=ProCashAccount.held_bonus + bonus_part,
            )
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)

        if result.rowcount == 0:
            # 밀렸다. 잔액이 진짜 부족한 것인지, 분배만 어긋난 것인지는
            # 다시 읽어서 판단한다. 낙관적 재시도.
            session.expire_all()
            continue

        charge = QuoteCharge(
            quote_id=quote_id,
            pro_id=pro_id,
            request_id=request_id,
            amount=amount,
            paid_portion=paid_part,
            bonus_portion=bonus_part,
            status="HOLD",
            hold_expires_at=expires_at,
        )
        session.add(charge)
        session.add(
            CashLedger(
                pro_id=pro_id,
                quote_id=quote_id,
                entry_type="HOLD",
                amount=amount,
                paid_delta=Decimal("0"),
                bonus_delta=Decimal("0"),
                memo="견적 발송, 보류 설정",
            )
        )
        try:
            await session.flush()
        except IntegrityError:
            # UNIQUE(quote_id) 위반 = 같은 견적이 동시에 두 번 들어왔다.
            # 애플리케이션이 놓쳐도 DB가 막는 마지막 방어선.
            await session.rollback()
            raise
        return charge

    raise ConcurrentUpdateExhausted("동시 요청이 몰려 보류를 잡지 못했습니다. 다시 시도해 주세요.")


async def capture(
    session: AsyncSession, *, quote_id: str, now: datetime | None = None
) -> QuoteCharge:
    """고객 열람 = 확정(CAPTURE). 여기서 처음으로 실제 차감이 일어난다.

    중복 열람 이벤트가 와도, 만료 처리와 동시에 도착해도 한 번만 과금된다.
    """
    now = now or utcnow()

    # 조건부 UPDATE. HOLD인 동안에만 CAPTURED로 넘어간다.
    result = await session.execute(
        update(QuoteCharge)
        .where(QuoteCharge.quote_id == quote_id, QuoteCharge.status == "HOLD")
        .values(status="CAPTURED", captured_at=now)
        .execution_options(synchronize_session=False)
    )

    charge = await _load_charge(session, quote_id)

    if result.rowcount == 0:
        # 선점당했다. 누가 선점했는지에 따라 의미가 다르다.
        if charge.status == "CAPTURED":
            # 중복 열람 이벤트. 이미 과금됐으니 같은 결과를 그대로 돌려준다(멱등).
            return charge
        # 만료 해제가 먼저 이겼다. 과금하지 않는 것이 맞다.
        raise ChargeAlreadyReleased(f"이미 보류가 해제된 견적입니다: {quote_id}")

    # 여기까지 왔으면 이 요청이 상태를 선점했다. 이제 실제로 차감한다.
    await session.execute(
        update(ProCashAccount)
        .where(ProCashAccount.pro_id == charge.pro_id)
        .values(
            paid_cash=ProCashAccount.paid_cash - charge.paid_portion,
            bonus_cash=ProCashAccount.bonus_cash - charge.bonus_portion,
            held_paid=ProCashAccount.held_paid - charge.paid_portion,
            held_bonus=ProCashAccount.held_bonus - charge.bonus_portion,
        )
        .execution_options(synchronize_session=False)
    )
    session.add(
        CashLedger(
            pro_id=charge.pro_id,
            quote_id=quote_id,
            entry_type="CAPTURE",
            amount=charge.amount,
            paid_delta=-charge.paid_portion,
            bonus_delta=-charge.bonus_portion,
            memo="고객 열람, 과금 확정",
        )
    )
    await session.flush()
    return charge


async def release(
    session: AsyncSession, *, quote_id: str, now: datetime | None = None
) -> QuoteCharge | None:
    """만료 해제(RELEASE). 48시간 안에 열람되지 않은 보류를 푼다.

    반환이 None이면 이 워커가 선점하지 못한 것이다. 오류가 아니다.
    워커가 두 번 돌아도, 스캔이 늦어도 결과가 같다.
    """
    now = now or utcnow()

    result = await session.execute(
        update(QuoteCharge)
        .where(
            QuoteCharge.quote_id == quote_id,
            QuoteCharge.status == "HOLD",
            QuoteCharge.hold_expires_at <= now,
        )
        .values(status="RELEASED", released_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        return None

    charge = await _load_charge(session, quote_id)
    await session.execute(
        update(ProCashAccount)
        .where(ProCashAccount.pro_id == charge.pro_id)
        .values(
            held_paid=ProCashAccount.held_paid - charge.paid_portion,
            held_bonus=ProCashAccount.held_bonus - charge.bonus_portion,
        )
        .execution_options(synchronize_session=False)
    )
    session.add(
        CashLedger(
            pro_id=charge.pro_id,
            quote_id=quote_id,
            entry_type="RELEASE",
            amount=charge.amount,
            paid_delta=Decimal("0"),
            bonus_delta=Decimal("0"),
            memo="48시간 미열람, 보류 해제",
        )
    )
    await session.flush()
    return charge


async def find_expired_quote_ids(
    session: AsyncSession, *, limit: int = 500, now: datetime | None = None
) -> list[str]:
    now = now or utcnow()
    rows = await session.execute(
        select(QuoteCharge.quote_id)
        .where(QuoteCharge.status == "HOLD", QuoteCharge.hold_expires_at <= now)
        .order_by(QuoteCharge.hold_expires_at)
        .limit(limit)
    )
    return list(rows.scalars())


async def _load_charge(session: AsyncSession, quote_id: str) -> QuoteCharge:
    row = await session.execute(select(QuoteCharge).where(QuoteCharge.quote_id == quote_id))
    charge = row.scalar_one_or_none()
    if charge is None:
        raise ChargeNotFound(f"견적 과금 건을 찾을 수 없습니다: {quote_id}")
    await session.refresh(charge)
    return charge


async def assert_ledger_matches_account(session: AsyncSession, pro_id: int) -> dict:
    """불변식 점검: 원장에서 재구성한 잔액이 계정 잔액과 같아야 한다.

    운영에서는 이걸 대사 배치로 돌린다. 여기서는 테스트가 호출한다.
    """
    row = await session.execute(
        text(
            """
            SELECT
              COALESCE(SUM(paid_delta), 0)  AS paid_delta,
              COALESCE(SUM(bonus_delta), 0) AS bonus_delta
            FROM cash_ledger WHERE pro_id = :pro_id
            """
        ),
        {"pro_id": pro_id},
    )
    paid_delta, bonus_delta = row.one()

    held = await session.execute(
        text(
            """
            SELECT
              COALESCE(SUM(paid_portion), 0)  AS held_paid,
              COALESCE(SUM(bonus_portion), 0) AS held_bonus
            FROM quote_charge WHERE pro_id = :pro_id AND status = 'HOLD'
            """
        ),
        {"pro_id": pro_id},
    )
    held_paid, held_bonus = held.one()

    acct = await get_account(session, pro_id)
    await session.refresh(acct)

    assert acct.held_paid == held_paid, f"held_paid 불일치: {acct.held_paid} != {held_paid}"
    assert acct.held_bonus == held_bonus, (
        f"held_bonus 불일치: {acct.held_bonus} != {held_bonus}"
    )
    return {"paid_delta": paid_delta, "bonus_delta": bonus_delta}
