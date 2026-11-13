import re
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Placeholder values shipped in .env.example. If one of them reaches a
# production deployment, the secret is public: refuse to start.
EXAMPLE_SECRET_KEYS = frozenset({"replace-with-a-long-random-string"})
MIN_PRODUCTION_SECRET_KEY_LENGTH = 32


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
    # Servidor MCP propio (mcp_server/, puerto 8100 en docker-compose) --
    # ChatService lo usa como cliente MCP para las tools de usuarios y
    # envios (ver app.chat.mcp_tools), en vez de llamarlas in-process
    # como antes (ver docs/roadmap.md seccion 2). "/mcp" es el path por
    # defecto que expone FastMCP para transport="streamable-http".
    mcp_server_url: str = "http://localhost:8100/mcp"
    # Token exchange (RFC 8693): ChatService NO reenvia el JWT del
    # usuario al MCP server; la API actua de STS y emite un token
    # delegado RS256 de vida corta con aud=mcp_audience, scope reducido
    # y claim `act`. Clave privada PEM (PKCS8) sin cifrar; si falta, en
    # development se genera una efimera al arrancar (los tokens mueren
    # al reiniciar), en cualquier otro entorno es un error. Generar con:
    # openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048
    sts_private_key: str | None = None
    sts_key_id: str = "sts-1"
    sts_issuer: str = "http://localhost:8000"
    mcp_audience: str = "mcp-server"
    # Browser origins allowed by CORS. Env var CORS_ORIGINS, comma
    # separated (e.g. "http://localhost:5173,https://app.example.com").
    # NoDecode: pydantic-settings must not try to parse it as JSON; the
    # validator below splits it. The default keeps the local Vite flow.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    # Un turno de chat abre UNA sesion MCP y puede tardar (LLM + hasta 5
    # vueltas de tools), asi que el TTL cubre un turno, no una llamada.
    delegated_token_ttl_seconds: int = 300
    # TTL del token de una accion aprobada por el humano: se usa
    # inmediatamente para esa unica llamada.
    approved_action_ttl_seconds: int = 60
    # Cuanto tiempo vive una accion pendiente de aprobacion.
    pending_action_ttl_minutes: int = 15
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
    # Google Drive (app.drive) usa el MISMO cliente OAuth que Gmail, con su
    # propia URI de retorno -- registrarla tambien como URI autorizada, y
    # habilitar la Google Drive API en el proyecto de Google Cloud.
    google_drive_redirect_uri: str = "http://localhost:8000/drive/callback"
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
    # Scheduler de kb_content/ (ver scripts/run_kb_ingestion_scheduler.py)
    # -- antes la unica forma de (re)ingestar era correr
    # scripts/ingest_kb_articles.py a mano. La ingesta es idempotente
    # (RAGService.ingest_article hace upsert por (organization_id,
    # titulo)), asi que correrla seguido no duplica nada. El intervalo
    # es mucho mas largo que el de Gmail (gmail_poll_interval_seconds)
    # porque el contenido de la KB cambia con poca frecuencia, no en
    # tiempo real.
    kb_ingestion_schedule_path: str = "kb_content/ingestion_schedule.yaml"
    kb_ingestion_poll_interval_seconds: int = 3600
    # Busqueda hibrida del RAG: vectorial + palabras clave fusionadas con
    # RRF (ver RAGService.retrieve). False vuelve a solo-vectorial sin
    # tocar codigo ni migraciones.
    rag_hybrid_search_enabled: bool = True
    # Filtro de relevancia del RAG: si el fragmento MAS cercano a la
    # pregunta esta a mas de esta distancia coseno, y ademas ningun
    # fragmento contiene TODOS los terminos de la pregunta, retrieve()
    # devuelve [] (el agente dice que no tiene la informacion en vez de
    # recibir 5 fragmentos irrelevantes y citarlos como fuentes). 0
    # desactiva el filtro. Calibrado con eval_harness/datasets/rag_golden.yaml
    # (preguntas con respuesta: 0.09-0.40; fuera de la KB: 0.35-0.49) para
    # gemini-embedding-001 a 768 dims -- si cambia el modelo de embeddings,
    # hay que recalibrarlo (python -m eval_harness.rag_eval <org> --compare).
    rag_max_cosine_distance: float = 0.35

    @field_validator("gmail_tracking_bare_pattern")
    @classmethod
    def _validate_tracking_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"GMAIL_TRACKING_BARE_PATTERN no es un regex valido: {exc}") from exc
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _validate_production_settings(self) -> "Settings":
        """Fail fast: refuse to boot production with development config.

        Messages name the offending field but never include its value.
        """
        if self.environment.strip().lower() != "production":
            return self

        problems: list[str] = []
        if len(self.secret_key) < MIN_PRODUCTION_SECRET_KEY_LENGTH:
            problems.append(
                f"SECRET_KEY must be at least {MIN_PRODUCTION_SECRET_KEY_LENGTH} characters"
            )
        if self.secret_key in EXAMPLE_SECRET_KEYS:
            problems.append("SECRET_KEY is still the placeholder value from .env.example")
        if not self.sts_private_key:
            problems.append(
                "STS_PRIVATE_KEY is required (every replica must sign with the same key)"
            )
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must not contain '*'")
        if any(origin != "*" and not origin.startswith("https://") for origin in self.cors_origins):
            problems.append("CORS_ORIGINS must contain only https:// origins")

        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    # hide_input_in_errors: a validation error must never echo the input
    # (it would print secrets such as SECRET_KEY into startup logs).
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

settings = Settings()
