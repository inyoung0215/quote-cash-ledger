class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InsufficientCash(DomainError):
    """사용 가능 잔액(잔액 - 보류 합계)이 부족하다."""

    status_code = 409
    code = "INSUFFICIENT_CASH"


class ConcurrentUpdateExhausted(DomainError):
    """동시 요청에 계속 밀려 재분배 재시도 한도를 넘겼다. 클라이언트 재시도 대상."""

    status_code = 409
    code = "CONCURRENT_UPDATE"


class ChargeNotFound(DomainError):
    status_code = 404
    code = "CHARGE_NOT_FOUND"


class ChargeAlreadyReleased(DomainError):
    """보류가 이미 해제된 뒤에 열람 이벤트가 도착했다.

    과금하지 않는 것이 맞다. 되돌리지 않고 사실만 기록한다.
    """

    status_code = 409
    code = "CHARGE_ALREADY_RELEASED"


class IdempotencyKeyConflict(DomainError):
    """같은 멱등 키로 다른 내용의 요청이 왔다."""

    status_code = 422
    code = "IDEMPOTENCY_KEY_CONFLICT"
