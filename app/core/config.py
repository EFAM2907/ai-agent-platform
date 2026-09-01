from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
  
    database_url: str

    # App
    app_name: str = "AI Agent Platform"
    environment: str = "development"
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()