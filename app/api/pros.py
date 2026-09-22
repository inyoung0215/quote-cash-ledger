from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import CashLedger
from app.schemas import CashResponse, LedgerEntryResponse
from app.services import cash

router = APIRouter(prefix="/v1/pros", tags=["pros"])


@router.get("/{pro_id}/cash", response_model=CashResponse)
async def get_cash(pro_id: int, session: AsyncSession = Depends(get_session)):
    acct = await cash.get_account(session, pro_id)
    return CashResponse(
        pro_id=acct.pro_id,
        paid_cash=acct.paid_cash,
        bonus_cash=acct.bonus_cash,
        held_paid=acct.held_paid,
        held_bonus=acct.held_bonus,
        available=acct.available,
    )


@router.get("/{pro_id}/ledger", response_model=list[LedgerEntryResponse])
async def get_ledger(pro_id: int, session: AsyncSession = Depends(get_session)):
    """원장 조회. 고수가 '왜 이 금액이 빠졌나'를 물었을 때 답이 되는 화면."""
    rows = await session.execute(
        select(CashLedger).where(CashLedger.pro_id == pro_id).order_by(CashLedger.id)
    )
    return list(rows.scalars())
