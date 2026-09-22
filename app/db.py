from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# 비동기 드라이버(aiomysql)를 쓴다.
# async 엔드포인트 안에서 동기 드라이버를 호출하면 그 호출이 끝날 때까지
# 이벤트 루프 전체가 멈추고 다른 요청도 함께 대기한다. docs/adr/0006 참고.
engine = create_async_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI Depends용 요청 단위 세션.

    yield 뒤의 정리 코드가 요청 종료 시점에 실행된다.
    Spring의 생성자 주입이 아니라 '요청마다 호출되는 의존성'이라는 점이 다르다.
    """
    async with SessionLocal() as session:
        yield session
