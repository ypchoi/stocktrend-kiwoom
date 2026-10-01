from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    kiwoom_port: int = Field(8028, validation_alias="KIWOOM_PORT")
    metadata_redis_url: str = Field(validation_alias="METADATA_REDIS_URL")
    # 키가 없어도 기동은 한다. 첫 호출에서 토큰 발급이 실패한다.
    kiwoom_app_key: Optional[str] = Field(None, validation_alias="KIWOOM_APP_KEY")
    kiwoom_secret_key: Optional[str] = Field(None, validation_alias="KIWOOM_SECRET_KEY")
    kiwoom_base_url: str = Field("https://api.kiwoom.com", validation_alias="KIWOOM_BASE_URL")
    # interval은 러너 전체가 공유하는 호출 시작 간격이다 (core RateLimitedApiQueue).
    # 키움 조회 한도는 공개 문서에 수치가 없다. 실측 전까지 초당 4회로 둔다.
    kiwoom_interval_secs: float = Field(0.25, validation_alias="KIWOOM_INTERVAL_SECS")
    kiwoom_api_workers: int = Field(2, validation_alias="KIWOOM_API_WORKERS")
    kiwoom_http_timeout_secs: float = Field(30.0, validation_alias="KIWOOM_HTTP_TIMEOUT_SECS")
    sync_retry_delay_mins: int = Field(5, validation_alias="KIWOOM_SYNC_RETRY_DELAY_MINS")
    log_level: str = Field("info", validation_alias="_LOG_LEVEL")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

# EventBus의 sender 값. stocktrend-manager가 이 값으로 알림을 라우팅한다.
SERVICE_NAME = "stocktrend-kiwoom"
