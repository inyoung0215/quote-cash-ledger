from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

# Pydantic이 하는 일은 '입력 형식' 검증까지다.
# 잔액이 충분한가 같은 비즈니스 규칙은 도메인 계층(services/cash.py)에 둔다.


class SendQuoteRequest(BaseModel):
    pro_id: int = Field(gt=0)
    quote_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0)

    @field_validator("amount", mode="before")
    @classmethod
    def to_decimal(cls, v):
        # Decimal(0.1) 이 아니라 Decimal('0.1') 이어야 한다.
        # float을 넘기면 이미 오차가 생긴 값이 들어온다. Java의 new BigDecimal(0.1)과 같은 함정.
        return Decimal(str(v))


class ChargeResponse(BaseModel):
    quote_id: str
    pro_id: int
    amount: Decimal
    paid_portion: Decimal
    bonus_portion: Decimal
    status: str
    hold_expires_at: datetime
    captured_at: datetime | None = None
    released_at: datetime | None = None

    model_config = {"from_attributes": True}


class CashResponse(BaseModel):
    pro_id: int
    paid_cash: Decimal
    bonus_cash: Decimal
    held_paid: Decimal
    held_bonus: Decimal
    available: Decimal


class LedgerEntryResponse(BaseModel):
    id: int
    quote_id: str
    entry_type: str
    amount: Decimal
    paid_delta: Decimal
    bonus_delta: Decimal
    memo: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ExpireResult(BaseModel):
    scanned: int
    released: int
    skipped: int
