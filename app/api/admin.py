from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import ExpireResult
from app.services import cash

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.post("/expire-holds", response_model=ExpireResult)
async def expire_holds(limit: int = 500, session: AsyncSession = Depends(get_session)):
    """만료된 보류를 해제한다. 워커와 같은 로직을 수동으로 돌리는 입구.

    여러 번 돌려도 결과가 같다. 이미 확정되거나 해제된 건은 조건부 UPDATE가
    0행을 반환하므로 건너뛴다.
    """
    quote_ids = await cash.find_expired_quote_ids(session, limit=limit)
    released = 0
    skipped = 0
    for quote_id in quote_ids:
        async with session.begin():
            result = await cash.release(session, quote_id=quote_id)
        if result is None:
            skipped += 1
        else:
            released += 1
    return ExpireResult(scanned=len(quote_ids), released=released, skipped=skipped)
