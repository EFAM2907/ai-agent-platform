import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
  
    database_url: str

    # App
    app_name: str = "AI Agent Platform"
    environment: str = "development"
    secret_key: str
    # Clave Fernet (32 bytes url-safe base64) usada para cifrar secretos
    # persistidos, ej. Organization.litellm_virtual_key. Generar con:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str
    algorithm: str
    access_token_expire_minutes: int
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    litellm_base_url: str = "http://localhost:4000"
    litellm_master_key: str | None = None
    # Virtual key acotada (budget bajo, un solo modelo barato) que
    # get_tenant_llm_client() usa cuando una organizacion todavia no
    # tiene su propia virtual key. Requerida a proposito (sin default):
    # la app debe fallar al arrancar si falta, nunca caer en silencio a
    # la master key -- ver Settings al final de este archivo.
    litellm_emergency_fallback_key: str
    emergency_fallback_daily_budget_usd: float = 2.0
    # Virtual key separada, solo para eval_harness/ -- NUNCA reutilizar
    # litellm_emergency_fallback_key acá: esa está restringida a
    # gemini-3.6-flash, pero los evals de routing necesitan poder
    # llamar también al modelo caro para medir accuracy de verdad.
    # Opcional (no fail-fast): a diferencia del fallback de emergencia,
    # nada del tráfico real de la app depende de esta -- solo
    # herramientas de desarrollo, que ya validan su presencia ellas
    # mismas antes de correr (ver eval_harness/model_routing_eval.py).
    litellm_eval_virtual_key: str | None = None
    eval_virtual_key_daily_budget_usd: float = 5.0
    default_tenant_monthly_budget: float = 10.0
    # OAuth 2.0 de Google para la integracion con Gmail (app.gmail).
    # Opcionales (no fail-fast): sin ellas la app arranca igual y solo
    # el flujo de conexion de Gmail responde 503. Crear el cliente OAuth
    # ("Aplicacion web") en console.cloud.google.com/apis/credentials y
    # registrar google_redirect_uri EXACTAMENTE igual como URI de
    # redireccionamiento autorizado.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/gmail/callback"
    # Ventana de busqueda del poller (sintaxis de Gmail: "2d", "12h").
    # Cubre una caida del poller de hasta esa duracion; los correos ya
    # atendidos se descartan por gmail_processed_messages, no por esta
    # ventana.
    gmail_poll_lookback: str = "2d"
    gmail_poll_interval_seconds: int = 60
    gmail_max_messages_per_poll: int = 20
    # Tope de respuestas automaticas al mismo remitente en 24h -- corta
    # cualquier ping-pong con un autoresponder que no se identifique
    # como tal y evita que un tercero use el buzon para spamear.
    gmail_max_replies_per_sender_per_day: int = 3
    # Tope de correos "necesito tu numero de guia" al mismo remitente en
    # 24h. Va aparte (y mas bajo) que el tope general: es una respuesta
    # a un correo SIN guia, la mas facil de provocar por un tercero.
    gmail_max_guide_requests_per_sender_per_day: int = 1
    # Solo se comparte el estado de un envio con quien escribe desde el
    # correo del cliente de ESE envio (Customer.email) y con la
    # autenticacion del remitente aprobada (SPF/DKIM/DMARC segun Gmail).
    # Sin esto, cualquiera que conozca una guia recibe su estado.
    gmail_require_sender_match: bool = True
    # Formato de una guia "suelta" (sin palabra clave "guia"/"tracking"
    # delante). Tras una palabra clave se aceptan tambien codigos con
    # letras y guiones. Regex sin anclas; ver app.gmail.tracking.
    gmail_tracking_bare_pattern: str = r"\d{6,15}"
    langfuse_api_url: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    
    @field_validator("gmail_tracking_bare_pattern")
    @classmethod
    def _validate_tracking_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"GMAIL_TRACKING_BARE_PATTERN no es un regex valido: {exc}") from exc
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
