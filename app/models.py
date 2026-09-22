from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 금액은 Numeric(문자열 기반 Decimal)으로만 다룬다.
# float은 2진 부동소수점이라 0.1을 정확히 표현하지 못하고, 원장 합산에서 오차가 누적된다.
MONEY = Numeric(18, 2)


class Base(DeclarativeBase):
    pass


class ProCashAccount(Base):
    """고수의 캐시 계정.

    잔액은 유상/보너스 두 재원으로 나뉘고, 각각 '보류(held)' 버킷을 따로 둔다.
    사용 가능 잔액 = (paid_cash - held_paid) + (bonus_cash - held_bonus)

    보류를 별도 컬럼으로 두는 이유:
    잔액을 먼저 깎아버리면 미열람 환급 때 어느 재원으로 돌려줄지 정보가 사라진다.
    차감 순서가 곧 고수의 손익이므로 재원을 끝까지 구분한다(ADR-0001).
    """

    __tablename__ = "pro_cash_account"

    pro_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    paid_cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    bonus_cash: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    held_paid: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    held_bonus: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def available(self) -> Decimal:
        return (self.paid_cash - self.held_paid) + (self.bonus_cash - self.held_bonus)


class QuoteCharge(Base):
    """견적 한 건의 과금 상태. 동시성 경쟁이 실제로 벌어지는 행.

    HOLD ──(고객 열람)──> CAPTURED
      └──(48h 미열람)───> RELEASED

    열람과 만료가 경계 시각에 동시에 일어나도, 두 처리 모두
    'status = HOLD 일 때만' 바꾸는 조건부 UPDATE라서 한쪽만 성공한다.
    """

    __tablename__ = "quote_charge"
    __table_args__ = (
        # 같은 견적에 과금 행이 두 개 생기지 않게 하는 마지막 방어선.
        # 애플리케이션 로직이 모두 뚫려도 DB가 막는다.
        UniqueConstraint("quote_id", name="uq_quote_charge_quote_id"),
        # 만료 스캔용. status + 만료시각으로 좁힌다.
        Index("ix_quote_charge_expire", "status", "hold_expires_at"),
        Index("ix_quote_charge_pro", "pro_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    quote_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pro_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)

    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # HOLD 시점에 확정한 재원 분배. CAPTURE/RELEASE 때 이 값 그대로 정산한다.
    paid_portion: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    bonus_portion: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    status: Mapped[str] = mapped_column(
        Enum("HOLD", "CAPTURED", "RELEASED", name="charge_status"), nullable=False
    )
    hold_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CashLedger(Base):
    """추가만 하는 원장(append-only). 어떤 행도 UPDATE하지 않는다.

    고수가 '왜 이 금액이 빠졌나'를 물었을 때 설명할 수 있어야 하기 때문이다.
    잔액을 덮어쓰면 그 순간 설명할 근거가 사라진다(ADR-0003).
    """

    __tablename__ = "cash_ledger"
    __table_args__ = (
        # 같은 견적에 같은 종류의 원장이 두 번 쌓이지 않게 한다.
        # 중복 열람 이벤트가 와도 과금은 한 번.
        UniqueConstraint("quote_id", "entry_type", name="uq_ledger_quote_entry"),
        Index("ix_ledger_pro", "pro_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pro_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quote_id: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_type: Mapped[str] = mapped_column(
        Enum("HOLD", "CAPTURE", "RELEASE", name="ledger_entry_type"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    paid_delta: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    bonus_delta: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    memo: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class IdempotencyRecord(Base):
    """멱등 키 저장소.

    클라이언트는 타임아웃이 나면 결과를 모르는 채로 재시도한다.
    같은 키면 같은 응답을 돌려줘야 견적이 두 건 발송되지 않는다.
    """

    __tablename__ = "idempotency_record"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(BigInteger, nullable=False)
    response_body: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
