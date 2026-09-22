"""벤치마크용 자원 풀.

숨고 기술 블로그의 '안심번호 할당' 문제를 단순화했다.
사용 가능한 번호가 여러 개 있고, 여러 요청이 동시에 '아무거나 하나'를 가져가려 한다.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BenchBase(DeclarativeBase):
    pass


class NumberPool(BenchBase):
    __tablename__ = "bench_number_pool"
    __table_args__ = (Index("ix_pool_status", "status", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("AVAILABLE", "ASSIGNED", name="pool_status"), nullable=False
    )
    assigned_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
