"""만료 보류 해제 워커.

이 워커는 늦게 돌아도, 두 번 돌아도, 여러 대가 동시에 돌아도 안전하다.
해제가 'status = HOLD 이고 만료 시각이 지났을 때만' 바꾸는 조건부 UPDATE라서,
같은 건을 두 워커가 집어도 한쪽만 성공하기 때문이다.

그래서 워커에 분산락을 걸지 않았다. 잠글 대상(행)이 이미 DB에 있으면
DB 원자성을 먼저 검토한다는 기준을 따랐다(ADR-0002).
"""

import asyncio
import logging

from app.db import SessionLocal
from app.services import cash

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("expire-holds")

INTERVAL_SECONDS = 60
BATCH_SIZE = 500


async def run_once() -> tuple[int, int]:
    released = 0
    skipped = 0
    async with SessionLocal() as session:
        quote_ids = await cash.find_expired_quote_ids(session, limit=BATCH_SIZE)
        for quote_id in quote_ids:
            async with session.begin():
                result = await cash.release(session, quote_id=quote_id)
            if result is None:
                # 열람이 먼저 이겼다. 정상이다.
                skipped += 1
            else:
                released += 1
    return released, skipped


async def main() -> None:
    log.info("만료 해제 워커 시작 (주기 %ds)", INTERVAL_SECONDS)
    while True:
        try:
            released, skipped = await run_once()
            if released or skipped:
                log.info("해제 %d건, 선점당해 건너뜀 %d건", released, skipped)
        except Exception:
            log.exception("배치 실패, 다음 주기에 다시 시도합니다")
        await asyncio.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
