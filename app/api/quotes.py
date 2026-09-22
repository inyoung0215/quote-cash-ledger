import json

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import DomainError
from app.schemas import ChargeResponse, SendQuoteRequest
from app.services import cash, idempotency

router = APIRouter(prefix="/v1/quotes", tags=["quotes"])


class IdempotencyInFlight(DomainError):
    """같은 멱등 키의 요청이 지금 동시에 처리되고 있다.

    둘 다 견적을 만들게 두는 것보다, 늦은 쪽을 물러서게 하고
    클라이언트가 다시 부르면 저장된 결과를 받게 하는 편이 안전하다.
    """

    status_code = 409
    code = "IDEMPOTENCY_IN_FLIGHT"


@router.post("", response_model=ChargeResponse, status_code=201)
async def send_quote(
    body: SendQuoteRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
):
    """견적 발송 → 캐시 보류(HOLD).

    Idempotency-Key 를 필수로 받는다. 클라이언트가 타임아웃 후 재시도해도
    견적이 두 건 나가지 않는다.

    조회와 기록과 보류를 하나의 트랜잭션 안에 둔다.
    조회만 먼저 커밋해 두면 그 사이에 다른 요청이 끼어들 수 있다.
    """
    payload = body.model_dump()

    async with session.begin():
        saved = await idempotency.find(session, idempotency_key, payload)
        if saved is not None:
            response.status_code = saved.response_status
            response.headers["Idempotent-Replay"] = "true"
            return json.loads(saved.response_body)

        charge = await cash.hold(
            session,
            pro_id=body.pro_id,
            quote_id=body.quote_id,
            request_id=body.request_id,
            amount=body.amount,
        )
        out = ChargeResponse.model_validate(charge).model_dump()
        try:
            await idempotency.save(session, idempotency_key, payload, 201, out)
        except IntegrityError as exc:
            # PK 충돌 = 같은 키로 동시에 들어온 다른 요청이 먼저 기록했다.
            # 이쪽 트랜잭션을 통째로 되돌려 견적이 두 건 생기지 않게 한다.
            raise IdempotencyInFlight(
                "같은 멱등 키의 요청이 처리 중입니다. 잠시 후 같은 키로 다시 호출하세요."
            ) from exc

    return out


@router.post("/{quote_id}/view", response_model=ChargeResponse)
async def view_quote(quote_id: str, session: AsyncSession = Depends(get_session)):
    """고객 열람 → 과금 확정(CAPTURE).

    같은 이벤트가 여러 번 와도 과금은 한 번이다.
    만료 해제가 먼저 이긴 경우에는 409 를 돌려주고 과금하지 않는다.
    """
    async with session.begin():
        charge = await cash.capture(session, quote_id=quote_id)
        return ChargeResponse.model_validate(charge)
