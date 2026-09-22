from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import admin, pros, quotes
from app.errors import DomainError

app = FastAPI(
    title="견적 캐시 원장 API",
    description=(
        "고수가 견적을 보낼 때 캐시를 보류하고, 고객이 열람하면 확정하고, "
        "48시간 안에 열람되지 않으면 해제하는 2단계 과금 구현입니다. "
        "숨고가 공개한 고수 안내 정책을 참고한 학습용 구현이며 실제 내부 구조와는 무관합니다."
    ),
    version="0.1.0",
)

app.include_router(quotes.router)
app.include_router(pros.router)
app.include_router(admin.router)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    # 재시도해도 되는 오류와 안 되는 오류를 클라이언트가 구분할 수 있게
    # 코드를 함께 내려준다. 비즈니스 거절을 5xx로 주면 중간 계층이
    # 재시도해도 되는 것으로 오해하고, 그게 곧 중복 과금이 된다.
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
