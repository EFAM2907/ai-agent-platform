from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
  
    database_url: str

    # App
    app_name: str = "AI Agent Platform"
    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()