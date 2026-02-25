from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    APP_NAME: str = "jjunmo-fastapi"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"  # local | staging | production

    DATABASE_URL: str = "sqlite:///./app.db"
    LOG_LEVEL: str = "DEBUG"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    @property
    def async_database_url(self) -> str:
        """Sync URL에서 async 드라이버 URL을 자동 생성"""
        url = self.DATABASE_URL
        if url.startswith("sqlite:///"):
            return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("mysql://"):
            return url.replace("mysql://", "mysql+aiomysql://", 1)
        return url

    @property
    def is_local(self) -> bool:
        return self.ENVIRONMENT == "local"


settings = Settings()
