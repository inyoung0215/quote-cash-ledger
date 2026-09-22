from datetime import UTC, datetime


def utcnow() -> datetime:
    """UTC 기준 현재 시각을 tz 정보 없는 datetime 으로 돌려준다.

    MySQL 의 DATETIME 은 타임존을 저장하지 않는다. 그래서 무엇을 넣을지
    애플리케이션이 정해야 하고, 여기서는 UTC 로 통일했다.

    `datetime.now()` 를 쓰면 서버의 로컬 시각이 들어간다.
    서버가 여러 대이거나 컨테이너의 TZ 설정이 다르면 같은 컬럼에 서로 다른
    기준의 시각이 섞이고, 48시간 만료 같은 시간 기반 판단이 조용히 어긋난다.
    한 번 섞이면 어느 행이 어느 기준인지 사후에 구분할 수 없다.
    """
    return datetime.now(UTC).replace(tzinfo=None)
