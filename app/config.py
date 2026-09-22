from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+aiomysql://root:localpw@127.0.0.1:3310/quote_cash"
    # 견적 발송 후 고객이 열람하지 않으면 보류가 해제되는 시간
    hold_ttl_hours: int = 48
    # HOLD 시 잔액 조건부 UPDATE가 동시 요청에 밀렸을 때 재분배 재시도 횟수
    hold_max_retry: int = 3


settings = Settings()
