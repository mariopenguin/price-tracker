from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    secret_key: str = "test-secret-key-for-development-only"
    database_url: str = "sqlite+aiosqlite:////data/db.sqlite3"
    telegram_bot_token: str = ""
    check_interval_hours: int = 6
    first_admin_email: str = ""
    first_admin_password: str = ""

settings = Settings()
