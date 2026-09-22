"""멱등 키 처리.

타임아웃이 난 클라이언트는 결과를 모르는 채로 재시도한다.
'실패'보다 '됐는지 모르는 상태'가 더 위험하다는 게 출발점이다.
"""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import IdempotencyKeyConflict
from app.models import IdempotencyRecord


def fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def find(session: AsyncSession, key: str, payload: dict) -> IdempotencyRecord | None:
    """저장된 결과가 있으면 돌려준다. 같은 키에 다른 내용이면 거부한다."""
    row = await session.execute(
        select(IdempotencyRecord).where(IdempotencyRecord.key == key)
    )
    record = row.scalar_one_or_none()
    if record is None:
        return None
    if record.request_fingerprint != fingerprint(payload):
        raise IdempotencyKeyConflict("같은 멱등 키로 다른 내용의 요청이 들어왔습니다.")
    return record


async def save(
    session: AsyncSession, key: str, payload: dict, status: int, body: dict
) -> None:
    """결과를 기록한다.

    PK 충돌이 나면 동시에 같은 키로 들어온 요청이 먼저 기록한 것이다.
    앞선 요청의 결과가 정답이므로 조용히 넘긴다.
    """
    session.add(
        IdempotencyRecord(
            key=key,
            request_fingerprint=fingerprint(payload),
            response_status=status,
            response_body=json.dumps(body, ensure_ascii=False, default=str)[:2000],
        )
    )
    # PK 충돌(IntegrityError)은 호출자가 잡는다.
    # 같은 키로 동시에 들어온 다른 요청이 먼저 기록했다는 뜻이다.
    await session.flush()
