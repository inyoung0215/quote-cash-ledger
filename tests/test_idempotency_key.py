"""멱등 키: 같은 요청을 두 번 보내도 견적은 한 건."""

import httpx
from asgi_lifespan import LifespanManager

from app.main import app


async def test_same_key_sends_quote_once(account):
    pro_id = await account()
    payload = {
        "pro_id": pro_id,
        "quote_id": "q-idem-1",
        "request_id": "req-1",
        "amount": "500",
    }
    headers = {"Idempotency-Key": "key-abc"}

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            first = await c.post("/v1/quotes", json=payload, headers=headers)
            second = await c.post("/v1/quotes", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.json()["quote_id"] == second.json()["quote_id"]
