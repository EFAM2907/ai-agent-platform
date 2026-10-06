# Dependencias del Proyecto — AI Agent Platform

Documento de referencia con las librerías instaladas, para qué sirve cada una, y cómo se están usando concretamente en el proyecto. Cubre los dominios `organizations`, `users`, `auth`, `chat`, `llm`, `rag` y `shipments`.

Última actualización: correspondiente al estado actual del proyecto tras agregar el gateway LLM (LiteLLM), RAG sobre una knowledge base con pgvector (incluyendo el pipeline de ingestión multi-formato — markdown, PDF, Excel — en `app/rag/ingestion/`), el agente de chat con tool-calling (streaming y no-streaming) sobre gestión de usuarios y consulta/actualización de envíos y clientes, el flujo de invitación de usuarios con contraseña temporal (`must_change_password`), la API REST de envíos/clientes (`app/shipments/api.py`, antes solo existía para `app/users/`), un servidor MCP propio con FastMCP (`mcp_server/`) que expone las mismas 10 tools por el protocolo MCP estándar, la migración del agente de chat para que sea cliente de ese servidor MCP en vez de llamar las tools in-process (`app.chat.mcp_tools` — reemplazó a `app.users.tools`/`app.shipments.tools`, que se borraron), un scheduler para la ingestión del RAG (`scripts/run_kb_ingestion_scheduler.py`, antes 100% manual) y un harness de evaluación de RAG con métricas de retrieval/faithfulness (`eval_harness/rag_eval.py`, antes solo existía para el `ModelRouter`), y la suite de tests (antes marcada como pendiente — ya está instalada y en uso activo).

---

## 1. Framework web

### `fastapi`
Framework principal de la API. Define los routers, valida requests/responses con Pydantic, maneja inyección de dependencias (`Depends`), respuestas en streaming (SSE) y genera la documentación interactiva en `/docs`.

**Uso en el proyecto:**
- `APIRouter` en cada dominio (`organizations/api.py`, `users/api.py`, `auth/api.py`, `chat/api.py`, `shipments/api.py`). `shipments` sí tiene router propio (4 endpoints — ver sección 6.1), construido para que el servidor MCP (sección 6.2) tuviera algo consistente que proxyear junto a `users`. `rag` sigue **sin router propio** — se accede únicamente desde el agente de chat vía tools, no como endpoints REST directos.
- `Depends()` para inyectar sesiones de BD, services, y el usuario autenticado (`get_db`, `get_user_service`, `get_current_user`, `get_chat_service`).
- `StreamingResponse` (`media_type="text/event-stream"`) para `POST /chat/messages/stream`: el turno completo del chat sale como Server-Sent Events (`start` → `delta`* → `done`/`error`), no como una respuesta JSON única.
- `CORSMiddleware` restringido a `http://localhost:5173` (dev server de Vite del frontend), no `"*"`.
- `HTTPException` para traducir excepciones internas (`UserAlreadyExistsError`, `InvalidCredentialsError`, `TenantVirtualKeyError`, etc.) a respuestas HTTP con el status code correcto.
- `Query` para parámetros de paginación (`skip`, `limit`) con validación (`le=100`).
- **Inyección y control de acceso robusto (`app/core/dependencies.py`):**
  - `require_owner(current_user)`: privilegios exclusivos de propietario.
  - `require_admin(current_user)`: operaciones de administración a usuarios `ADMIN` u `OWNER`.
  - `require_password_changed(current_user)`: bloquea con 403 las rutas de negocio (hoy: `POST /chat/messages` y `/chat/messages/stream`) mientras la cuenta tenga `must_change_password=True` — nunca se aplica a `/auth/*` (sería una trampa sin salida) ni a rutas self-service sobre la propia cuenta.
  - `verify_same_organization(admin, target_organization_id)`: restringe acciones a la misma organización, con bypass para administradores de plataforma globales (`is_platform_admin`).
  - `can_manage_other_user(actor, target)`: compara niveles de `ROLE_HIERARCHY` para autorizar modificaciones de usuarios.

### `uvicorn`
Servidor ASGI que corre la aplicación FastAPI. Se usa con `--reload` en desarrollo para recargar automáticamente al detectar cambios en el código.

---

## 2. Base de datos y ORM

### `sqlalchemy` (v2.0, sintaxis async)
ORM usado para modelar todas las tablas (`Organization`, `User`, `RefreshToken`, `ChatSession`/`ChatMessage`, `KBArticle`/`KBChunk`, `Customer`/`Shipment`/`ShipmentEvent`) y ejecutar queries con `select()` async.

**Uso en el proyecto:**
- Modelos con `Mapped`/`mapped_column`, UUID como primary key, `relationship()` bidireccional (`Organization`↔`User`, `Shipment`↔`Customer`↔`ShipmentEvent`), `TYPE_CHECKING` para evitar imports circulares.
- `create_async_engine`, `async_sessionmaker` con `expire_on_commit=False`.
- `selectinload()` para precargar relaciones (`Shipment.customer`, `Shipment.events`) — necesario en SQLAlchemy async: acceder a una relación no cargada fuera de un `await` explícito revienta con `MissingGreenlet`.
- Mutación de una relación ya cargada vía `shipment.events.append(event)` (no `ShipmentEvent(shipment_id=...)` + `session.add()`) para que el ORM setee el FK y actualice la colección en memoria a la vez.
- **Transacciones complejas y atómicas:**
  - Creación conjunta de organización y su primer `OWNER` en un único commit (`OrganizationService.create_with_owner`).
  - Actualizar `Shipment.status` y agregar su `ShipmentEvent` correspondiente en la misma transacción (`ShipmentService.update_shipment_status`) — nunca uno sin el otro, para no romper la garantía de que `shipment_events` es la fuente de verdad del historial.
  - Bloqueo de filas (`.with_for_update()`) en `UserRepository.get_by_id` durante la transferencia de propiedad.

### `asyncpg`
Driver de PostgreSQL para SQLAlchemy en modo async.

### `psycopg2-binary`
Driver síncrono de PostgreSQL, usado exclusivamente por Alembic (ver abajo).

### `pgvector`
Extensión de PostgreSQL + tipo `Vector` para SQLAlchemy. Almacena los embeddings de la knowledge base (`KBChunk.embedding`, 768 dimensiones) y permite búsqueda por similitud coseno. La imagen de base de datos del proyecto es `pgvector/pgvector:pg16` (drop-in de `postgres:16` con la extensión ya compilada), no `postgres:16` a secas.

### `alembic`
Herramienta de migraciones. Configurado con el driver síncrono (`psycopg2`) porque Alembic no soporta drivers async de forma nativa en su configuración estándar — `migrations/env.py` reescribe la URL de `postgresql+asyncpg://` a `postgresql+psycopg2://` en tiempo de ejecución.

**Cadena de migraciones actual** (`alembic heads` debe dar un único head): organizations → refresh_tokens → roles/platform admin → users → email único activo → RAG (pgvector) → chat → shipments → `must_change_password`.

- **Restricciones complejas aplicadas vía Alembic:**
  - `ix_users_email_unique_active` / `ix_shipments_tracking_number_unique_active`: índices únicos parciales que solo aplican a filas activas (`deleted_at IS NULL`) — permiten reutilizar un email o un tracking number si la fila anterior fue borrada lógicamente.
  - `ix_users_one_active_owner_per_organization`: máximo un `OWNER` activo por organización.
  - Enums de Postgres compartidos entre varias tablas (`shipmentstatus` en `shipments.status` y `shipment_events.event_type`) deben crearse con `postgresql.ENUM(..., create_type=False)`, no `sa.Enum` genérico — de lo contrario `create_table` intenta re-crear el tipo en la segunda tabla que lo usa y falla con `DuplicateObject` (bug real, ya corregido).

---

## 3. Validación de datos

### `pydantic` (v2)
Define los schemas de entrada/salida de cada endpoint.

**Uso en el proyecto:**
- `OrganizationBootstrap`, `OwnershipTransfer`, `UserCreate` (sin `password` — ver sección 6), `UserInviteResponse` (con `temporary_password` en texto plano, una sola vez), `ChatMessageIn`/`ChatMessageOut`/`ChatReplyOut`/`ChatSessionOut`, `TokenPair` (con `must_change_password`), `PasswordChangeRequest`.
- Separación estricta entre schemas de entrada y de salida para nunca exponer credenciales (`UserResponse` nunca incluye `hashed_password`).
- `model_dump(exclude_unset=True)` en updates parciales (PATCH).
- `EmailStr` para validación de formato de email.

### `email-validator`
Dependencia adicional requerida por Pydantic para que `EmailStr` funcione.

### `pydantic-settings`
Configuración vía variables de entorno tipada y validada en `core/config.py` — incluye credenciales de LiteLLM/proveedores, presupuesto por defecto de tenant, clave de cifrado, y falla al arrancar (`fail-fast`) si falta `litellm_emergency_fallback_key`.

### `jsonschema`
Valida la salida estructurada del LLM contra un schema (`LLMRequest.response_schema`) en el loop de reparación de `LLMClient` — si el modelo devuelve JSON inválido o que no cumple el schema, se le pide corregir con el error exacto antes de fallar definitivamente.

### `PyYAML`
Carga los prompts versionados del sistema (`app/llm/prompts/templates/**/v*.yaml`) — cada prompt (`support_chat_agent`, `route_model_complexity`) tiene versiones explícitas para poder comparar resultados de forma reproducible.

---

## 4. Seguridad

### `argon2-cffi`
Hasheo de contraseñas recomendado por OWASP.

**Uso en el proyecto:**
- `hash_password(password)`: hashea antes de guardar en `hashed_password`.
- `verify_password(plain_password, hashed_password)`: compara de forma segura, manejando `VerifyMismatchError`/`InvalidHashError`.
- `generate_temporary_password()`: genera una contraseña temporal aleatoria (`secrets.token_urlsafe(12)`) para cuentas invitadas — ver sección 6.

### `pyjwt`
Genera y decodifica los JWT de sesión.

**Uso en el proyecto:**
- `create_access_token(data)`: firma con `SECRET_KEY` y `HS256`.
- `decode_access_token(token)`: verifica firma y expiración; lanza `InvalidTokenError` si expiró o es inválido.
- Refresh tokens de larga duración (30 días) generados aparte (`secrets.token_urlsafe(64)`), hasheados con SHA-256 antes de persistirse (`app/auth/repository.py`) — nunca se guarda el refresh token en claro.

### `cryptography`
Cifrado simétrico (Fernet) para secretos que se persisten en la base de datos.

**Uso en el proyecto (`app/core/crypto.py`):**
- `encrypt_secret`/`decrypt_secret`: usados exclusivamente para `Organization.litellm_virtual_key` — una virtual key de LiteLLM equivale a una API key real (autentica y gasta contra el budget del tenant), así que nunca queda en texto plano en la base de datos.

---

## 5. Rate limiting

### `redis`
Cliente async de Redis. Backend de un rate limiter de ventana fija (`app/core/rate_limit.py`).

**Uso en el proyecto:**
- `rate_limit`: 100 requests/60s por usuario autenticado, aplicado a nivel de router en `organizations` y `users`.
- `login_rate_limit`: 5 intentos/60s en `POST /auth/login` y `POST /auth/register`.
- `refresh_rate_limit`: 10/60s en `POST /auth/refresh`.
- Fail-open a propósito: si Redis no responde, se loguea un warning y se deja pasar el request — un rate limiter caído no debe tumbar la API entera.

---

## 6. Gateway LLM y agente de chat

### `openai` (SDK)
Cliente HTTP usado para hablar con el gateway **LiteLLM**, no directamente con OpenAI — la app nunca importa un SDK de proveedor (Anthropic, Google) fuera de `app/llm/legacy/` (código muerto, reemplazado). `AsyncOpenAI(base_url=litellm_base_url, api_key=<virtual_key>)` apunta al proxy; la superficie OpenAI-compatible (`/chat/completions`, `/embeddings`) es lo que permite que LiteLLM unifique Gemini, OpenAI y Anthropic detrás de un único contrato.

**Uso en el proyecto (`app/llm/litellmprovider/litellm_provider.py`):**
- `generate()`: no-streaming, con soporte de `tools` (function calling) y reasoning_effort.
- `generate_stream()`: streaming token a token, **también con soporte de `tools`** — los `tool_calls` llegan fragmentados por chunk (id/nombre en el primero, argumentos de a pedazos en los siguientes) y se reensamblan por índice antes de exponerse en `LLMResponse.tool_calls` vía un `StreamUsageCollector`.
- Timeout explícito de 60s en el cliente (antes no había ninguno — el SDK por defecto espera hasta 10 minutos, lo que permitía que un solo request colgara el turno de chat mucho más de lo tolerable si el gateway o el proveedor se colgaban).
- Taxonomía de errores propia (`app/llm/errors.py`): `RateLimitError`, `TimeoutError_`, `ProviderError` (5xx/transporte), `InvalidRequestError` (400 — nunca se reintenta, a diferencia de `ProviderError`), `ContentFilterError`, `InvalidResponseError`.

### LiteLLM (servicio Docker, no una librería Python)
Gateway self-hosted (`ghcr.io/berriai/litellm:v1.83.10-stable`, versión pineada tras un incidente de supply-chain) que centraliza el ruteo/fallback entre proveedores (`litellm_config.yaml`), aplica presupuesto y aislamiento por tenant vía virtual keys, y loguea trazas a Langfuse.

**Modelos configurados hoy:** `gemini-3.6-flash` (barato), `claude-sonnet-5` (caro — actualmente apuntado *temporalmente* a `gemini-3.6-flash` en `app/chat/service.py` mientras Anthropic/OpenAI no tienen crédito real, ver sección 9), `claude-haiku-4-5`, `gpt-5.6-luna`, `gemini-embedding`, y `deepseek-chat` (proveedor DeepSeek vía su API, prepago sin free tier, agregado como segundo fallback barato para el par `gemini-3.1-flash-lite`/`gemini-3.5-flash-lite` — el problema que resuelve es el rate-limit por RPM de Gemini, no precio). Fallbacks configurados para `gemini-3.6-flash`, `claude-sonnet-5` (evitando caer primero en `claude-haiku-4-5`, que comparte la misma API key de Anthropic), y el par flash-lite (que cae en su hermano y luego en `deepseek-chat` antes de escalar a proveedores sin crédito).

- **Reintentos: responsabilidad exclusiva de LiteLLM** (`router_settings.num_retries`), no de la app — `LLMClient` ya no reintiene nada por su cuenta. Antes reintentaba también él mismo, lo que bajo rate-limit sostenido significaba `app_retries × litellm_retries` llamadas reales por una sola generación (incidente real: ~170s de espera para una consulta simple).
- Virtual keys por tenant (`app.llm.litellm_admin`): una por organización, aprovisionada en background al registrarse (`BackgroundTasks`), más una de emergencia (budget diario bajo, un solo modelo) para tenants sin la propia todavía, y una separada para `eval_harness/`.

### `app/llm/client.py` (`LLMClient`) — fachada propia, no una librería externa
Único punto de entrada al gateway para el resto de la app. Se ocupa solo de lo que LiteLLM no puede resolver por sí solo: el loop de reparación de salida estructurada (JSON que no cumple el schema pedido se reintenta con el error exacto, hasta 2 veces).

### `app/llm/tools.py` — tool-calling loop, no una librería externa
`run_tool_loop()` (no-streaming) y `run_streaming_tool_loop()` (streaming real, incluso con tools de por medio): ejecutan cada `tool_call` que el LLM pide, le devuelven el resultado, y repiten hasta que responde con texto final o se agota `max_iterations` (`ToolLoopError` si nunca converge). Una excepción de una tool nunca tumba el loop — se convierte en `{"error": ...}` y el LLM decide qué hacer con eso.

### `app/llm/routing.py` (`ModelRouter`) — sin librería externa
Clasifica cada mensaje como barato/caro con una llamada al modelo barato (`route_model_complexity` prompt, salida estructurada) antes de decidir qué modelo usa la respuesta real. Salta esa llamada solo para mensajes estructuralmente triviales (`_is_obviously_trivial`: saludos/cierres muy cortos) — nunca como sustituto del juicio del clasificador sobre contenido real. Ante fallo del clasificador, por defecto usa el modelo caro (fail-safe hacia "más capaz", no hacia "más barato").

### Tools expuestas al agente de chat hoy

**Usuarios** (`app/users/tools.py`, jerarquía de roles vía `ROLE_HIERARCHY`, aislamiento multi-tenant): `create_user` (ADMIN/OWNER, genera contraseña temporal, un ADMIN no puede crear otro ADMIN, nunca OWNER), `list_users` (ADMIN/OWNER), `get_user` (self-service o ADMIN/OWNER), `update_user` (self-service o outranking — nunca contraseña, ver más abajo), `delete_user` (outranking, un OWNER no puede autoeliminarse), `change_user_role` (ADMIN/OWNER, outranking, nunca asigna OWNER). Cada handler reaplica exactamente las mismas reglas que la API REST — llamar una tool desde el chat nunca hace nada que el usuario no pudiera hacer llamando la API directo.

**Envíos y clientes** (`app/shipments/tools.py`): `list_shipments`, `get_shipment_status`, `list_customers` (sin restricción de rol — datos de negocio del tenant, no cuentas de plataforma) y `update_shipment_status` (cualquier rol excepto VIEWER — es una operación logística rutinaria, no administración de cuenta; actualiza `Shipment.status` y agrega el `ShipmentEvent` correspondiente en la misma transacción).

### 6.1. API REST de envíos (`app/shipments/api.py`)

Hasta hace poco `app/shipments/` solo existía como tools de chat, sin equivalente REST (a diferencia de `app/users/`, que siempre tuvo los dos caminos). Se agregaron 4 endpoints que reusan `ShipmentService` tal cual — ninguna lógica de negocio nueva, solo una superficie HTTP adicional sobre lo que ya existía:

- `GET /shipments/` y `GET /shipments/customers`: paginados (`skip`/`limit`, `limit` con `le=100`).
- `GET /shipments/{tracking_number}/status`: 404 si el tracking number no existe en la organización del usuario autenticado.
- `PATCH /shipments/{tracking_number}/status`: 403 si el usuario es `VIEWER`, 400 si `new_status` no es un `ShipmentStatus` válido, 404 igual que el anterior.

`/customers` se declara antes de `/{tracking_number}/status` en el router — mismo cuidado defensivo que ya existía en `users/api.py` con `/users/me` antes de `/users/{user_id}`, para que FastAPI no intente matchear `"customers"` como si fuera un tracking number. La serialización (`serialize_customer`, `serialize_shipment_summary`, `serialize_shipment_detail`) vive en `app/shipments/schemas.py` — único consumidor hoy, ver sección 6.2 para por qué el chat ya no necesita su propia copia. Tests en `tests/test_shipments_api.py`.

### 6.2. Servidor MCP propio (`mcp_server/`), y el chat como su cliente (`app/chat/mcp_tools.py`)

Servidor [FastMCP](https://github.com/jlowin/fastmcp) (`transport="streamable-http"`) que expone las mismas 10 operaciones de usuarios y envíos por el protocolo MCP estándar — ver `docs/roadmap.md` sección 2 para el por qué.

Diseño de **proxy delgado**: cada `@mcp.tool` (`mcp_server/server.py`) reenvía la llamada como un request HTTP normal a la API REST que ya existe (`app.users.api`, `app.shipments.api`), reusando el mismo Bearer token que el cliente MCP ya trae (`get_http_headers()` de `fastmcp.server.dependencies`). Dos ganancias de esa decisión: el contenedor del servicio queda liviano (solo `fastmcp`+`httpx` — sin SQLAlchemy/asyncpg/cryptography, lo más pesado de la imagen de la API principal), y la identidad del usuario cruza el proceso gratis — la API principal valida el token exactamente igual que a cualquier otro cliente, sin lógica de autorización propia duplicada en este servidor. Un error de negocio (401/403/404 de la API) se traduce a `{"error": ..., "status_code": ...}` en vez de tumbar la tool call.

Limitación a propósito: solo soporta `streamable-http`, no `stdio` — `get_http_headers()` necesita un request HTTP real detrás para leer el token, algo que no existe con transporte stdio. Tiene su propio `Dockerfile` y requirements (`mcp_server/requirements.txt`) y servicio en `docker-compose.yml` (`mcp-server`, puerto 8100 — ver sección 9). Tests en `tests/test_mcp_server.py` (mockean `httpx.AsyncClient`/el `request` del cliente, no necesitan la API real corriendo).

**Ya no es un camino adicional opcional: el chat lo usa como su único cliente MCP.** `ChatService` dejó de llamar `app.users.tools`/`app.shipments.tools` in-process (ambos módulos se borraron) — `app.chat.mcp_tools.open_mcp_tools(bearer_token)` abre una sesión `fastmcp.client.Client` contra `settings.mcp_server_url` (env var `MCP_SERVER_URL`, default `http://localhost:8100/mcp`) reenviando el Bearer token del usuario, pide `list_tools()`, y traduce cada `mcp_types.Tool` a un `app.llm.tools.Tool` genérico sin volver a declarar el JSON schema a mano (lo anuncia el propio servidor MCP). `ChatService.send_message`/`send_message_stream` envuelven el `run_tool_loop`/`run_streaming_tool_loop` completo dentro de ese `async with` — una sesión por turno, viva mientras dure el loop de varias vueltas, no una conexión por tool call.

Consecuencia real de este cambio: `mcp-server` pasa de ser opcional a ser una dependencia dura del chat — si no está levantado, ninguna conversación con tools funciona (ni una que no termine llamando ninguna, porque la lista de tools se pide al abrir la sesión). También significa que el `requirements.txt` raíz **ya no está libre de `fastmcp`**: ahora instala `fastmcp-slim` (la variante sin extras de servidor — el cliente no necesita `cyclopts`/`uvicorn`-para-CLI/etc. que sí carga `mcp_server/requirements.txt` del lado del servidor) para poder importar `fastmcp.client`. Se verificó una instalación combinada real contra los pines existentes (`starlette==1.6.0`, `uvicorn==0.52.3`, `pydantic==2.13.4`, etc.): todos satisfacen las cotas mínimas que pide `mcp==2.2.0`/`fastmcp-slim==4.0.10`, así que ninguno de los pines pre-existentes subió de versión — solo se agregaron los paquetes nuevos (`mcp`, `mcp-types`, `Authlib`, `opentelemetry-api`, `py-key-value-aio` y sus dependencias, etc.).

---

## 7. RAG (Retrieval-Augmented Generation)

Sin librería de orquestación externa (LangChain, LlamaIndex, etc.) — implementado directo sobre `pgvector` + el gateway de embeddings.

**Uso en el proyecto (`app/rag/`):**
- `chunking.py`: parte un artículo de la KB en fragmentos.
- `embeddings.py` (`EmbeddingClient`): un vector de 768 dimensiones por chunk, vía `gemini-embedding` en LiteLLM (mismo patrón que `LiteLLMProvider` pero contra `/embeddings`, no `/chat/completions`).
- `service.py` (`RAGService.ingest_article`): crea el artículo, lo chunkea, embeddea y guarda todo en una sola transacción — nunca queda a medio indexar.
- `RAGService.retrieve`: embeddea la pregunta del usuario y devuelve los `top_k` chunks más parecidos (distancia coseno) de la KB de esa organización, sin aplicar un umbral de relevancia (esa decisión es del caller).
- Ingesta vía `scripts/ingest_kb_articles.py <organization_id> [--dir <tenant>]` — sin endpoint REST de ingesta. Soporta markdown, PDF y Excel en la misma carpeta (`app/rag/ingestion/`, ver `docs/roadmap.md` sección 3): un loader por formato normaliza cualquier fuente a `IngestedDocument`, y `registry.load_documents_from_dir` elige el loader por extensión, salteando con un warning lo que no sabe leer en vez de abortar toda la carpeta. Idempotente: `RAGService.ingest_article` hace upsert por `(organization_id, title)`, así que correr el script dos veces no duplica artículos.
- Ya no es puramente manual: `scripts/run_kb_ingestion_scheduler.py` corre esa misma ingesta periódicamente (modo `--once` para cron del SO, o bucle continuo cada `kb_ingestion_poll_interval_seconds`), leyendo los pares `(organization_id, subcarpeta)` desde `kb_content/ingestion_schedule.yaml` (no versionado — específico de cada deployment, ver `kb_content/ingestion_schedule.yaml.example`). La fuente sigue siendo exclusivamente `kb_content/` local — no hay conectores a sistemas externos (ver `docs/roadmap.md` sección 3).
- `eval_harness/rag_eval.py` + `eval_harness/datasets/rag_golden.yaml` (ver `docs/roadmap.md` sección 6): mismo patrón que `eval_harness/model_routing_eval.py` pero midiendo RAG — precisión/recall de `RAGService.retrieve` contra un dataset de preguntas con fuente esperada, y faithfulness de una respuesta generada contra lo recuperado, vía un juez LLM barato. Sin test dedicado, a propósito (llama LLMs reales, mismo motivo que `model_routing_eval.py`).

---

## 8. Testing

**Antes marcado como pendiente en este documento — ya instalado y en uso activo, no pendiente.**

### `pytest` / `pytest-asyncio`
Suite de tests async contra una base de datos Postgres real de pruebas (`ai_agent_platform_test`), no mocks de la capa de datos.

**Uso en el proyecto:**
- `tests/conftest.py`: fixture `db_session` (engine propio por test, transacción que se revierte al final — ningún test deja residuo) y fixture `client` (`httpx.AsyncClient` + `ASGITransport` contra la app real, con `get_db` sobreescrito para compartir la misma sesión/transacción del test).
- `tests/factories.py`: helpers (`create_organization`, `create_user`, `create_customer`, `create_shipment`) para armar estado de dominio sin pasar por HTTP.
- Cobertura actual: RBAC y aislamiento multi-tenant end-to-end (`test_tenant_isolation.py`), los endpoints REST de usuarios y de envíos (permisos, jerarquía, cross-tenant — `test_shipments_api.py` y los de `users`), el tool-calling loop genérico (streaming y no-streaming), `ModelRouter` (clasificación + heurístico de mensajes triviales), `LiteLLMProvider` (mapeo de errores, reensamblado de tool_calls en streaming), `LLMClient` (ya no reintenta nada), `require_password_changed`, el flujo de invitación de usuarios (`UserService.create`), la app de LiteLLM (admin, provisioning), el pipeline de ingestión multi-formato del RAG (`test_rag_ingestion.py`, `test_rag_service_ingest.py`), el servidor MCP (`test_mcp_server.py` — mockea `httpx.AsyncClient`, no necesita la API real corriendo) y el cliente MCP del chat (`test_chat_mcp_tools.py` — mockea `fastmcp.client.Client`, verifica que `open_mcp_tools` traduce `mcp_types.Tool` a `app.llm.tools.Tool` y reenvía el Bearer token).
- `tests/test_user_management_tools.py` y `tests/test_shipment_tools.py` (las tools in-process `app.users.tools`/`app.shipments.tools`) se borraron junto con los módulos que probaban: el chat ya no las llama, quedaron huérfanos (ver sección 6.2).
- Los módulos de test que quedaron huérfanos tras el reemplazo de los providers directos por el gateway LiteLLM (`test_anthropic_provider.py`, `test_gemini*.py`, `test_llmclient.py`, `test_openai_provider.py` — importaban `app.llm.providers`, movido a `app.llm.legacy`) ya se borraron: quedan completamente cubiertos por `test_litellm_provider.py` y `test_llm_client_retries.py`.

### `httpx`
Cliente HTTP async — usado tanto por los tests (`AsyncClient` contra la app) como por `app.llm.litellm_admin` para hablar con el proxy LiteLLM.

---

## 9. Infraestructura (Docker Compose)

- **`db`** (`pgvector/pgvector:pg16`): Postgres de la app, puerto `5433`.
- **`redis`** (`redis:7-alpine`): rate limiting.
- **`litellm`** (`ghcr.io/berriai/litellm:v1.83.10-stable`): gateway LLM, puerto `4000`, config montada desde `litellm_config.yaml`.
- **`litellm-db`** (`postgres:16`): base de datos propia de LiteLLM (virtual keys, spend, teams) — separada de la de la app a propósito, distinto dominio de fallo.
- **`mcp-server`** (build propio, `mcp_server/Dockerfile`): el servidor MCP (sección 6.2), puerto `8100`. No está en el mismo `docker-compose.yml` que la API principal porque esa API no corre en Docker en este proyecto — se levanta aparte con `uvicorn` en el host — así que este contenedor alcanza la API vía `host.docker.internal` (funciona tal cual en Docker Desktop; en Linux nativo sin Docker Desktop hay que pasar `MCP_SERVER_API_BASE_URL` con la IP del host a mano).

**Estado operacional real, no solo de infraestructura:** Anthropic y OpenAI están sin crédito de facturación; Gemini tiene una cuota gratuita diaria muy baja (20 requests/día para `gemini-3.6-flash`) que se agota fácilmente. Por eso `EXPENSIVE_MODEL` en `app/chat/service.py` está temporalmente apuntado a `gemini-3.6-flash` en vez de `claude-sonnet-5` — marcado explícitamente como `# TEMPORAL`, a revertir cuando haya crédito real.

---

## 10. Resumen de flujos y capacidades actuales

1. **Bootstrap de organización** (`POST /organizations/`): crea `Organization` + su primer `OWNER` de forma atómica, dispara aprovisionamiento de virtual key de LiteLLM en background.
2. **Invitación de usuarios** (`POST /users/`, o la tool `create_user` desde el chat): quien invita **nunca** elige la contraseña — se genera una temporal (`must_change_password=True`). Si la organización tiene Gmail conectado, esa contraseña se manda por correo al usuario nuevo y nunca aparece en la respuesta (`password_sent_by_email=true`, `temporary_password=null`); si no hay Gmail conectado, o el envío falla por lo que sea, se devuelve en texto plano una única vez, como antes. Ya no son "dos caminos que comparten lógica": la tool de chat reenvía a este mismo endpoint vía el servidor MCP (sección 6.2), así que es un solo camino real.
3. **Login / refresh / logout / cambio de contraseña** (`POST /auth/login|refresh|logout|change-password`): JWT de acceso + refresh token de 30 días hasheado; `change-password` es la única ruta que apaga `must_change_password`.
4. **RBAC y aislamiento multi-tenant** en cada operación sobre usuarios/organizaciones: jerarquía de roles (`OWNER > ADMIN > MEMBER > VIEWER`), aislamiento por `organization_id` con bypass explícito solo para `is_platform_admin`.
5. **Transferencia de propiedad** (`POST /organizations/{id}/transfer-ownership`): swap atómico de roles con bloqueo de filas.
6. **Chat con tool-calling, streaming real** (`POST /chat/messages` y `/messages/stream`): cada turno recupera contexto de la KB (RAG) y elige modelo barato/caro en paralelo, arma el prompt versionado, y corre el loop de tools (usuarios + envíos/clientes, servidas por el servidor MCP — sección 6.2) hasta obtener una respuesta final — bloqueado con 403 si la cuenta tiene un cambio de contraseña pendiente. Una respuesta vacía del LLM (pasa bajo rate-limit en cascada) se trata como error explícito, no como éxito silencioso.
7. **Gestión de usuarios desde el chat, en lenguaje natural**: invitar, listar, ver, actualizar, eliminar y cambiar de rol — vía el servidor MCP, que reenvía a la API REST con el Bearer token del usuario, así que corre con exactamente las mismas reglas de autorización que llamar la API REST directo.
8. **Consulta y actualización de envíos/clientes desde el chat**: cuántos envíos hay y de qué clientes, estado y último evento de un envío puntual, lista de clientes, y marcar un envío con un nuevo estado (registrando el evento correspondiente) — mismo camino vía MCP que el punto 7.
9. **Evaluación de la calidad del routing** (`eval_harness/model_routing_eval.py` + `datasets/model_routing_golden.yaml`): mide accuracy real del clasificador barato/caro contra un dataset etiquetado a mano, en vez de asumirla.
10. **Evaluación de la calidad del RAG** (`eval_harness/rag_eval.py` + `datasets/rag_golden.yaml`): mide precisión/recall de retrieval y faithfulness de la respuesta generada contra un dataset de preguntas con fuente esperada, mismo principio que el punto anterior aplicado a la KB en vez de al routing.
11. **Ingestión programada del RAG** (`scripts/run_kb_ingestion_scheduler.py`): corre la ingesta de `kb_content/` para cada organización configurada en `kb_content/ingestion_schedule.yaml`, sin depender de que alguien la invoque a mano — modo `--once` (cron del SO) o bucle continuo.

---

## 11. Scripts operacionales (`scripts/`)

- `seed_inter_rapidisimo_shipments.py`: siembra clientes/envíos/eventos de ejemplo para el tenant Inter Rapidísimo (no idempotente — re-correr duplica o falla por el índice único de tracking number).
- `ingest_kb_articles.py`: indexa `kb_content/` en la KB (RAG). Idempotente, reusable desde otros scripts (ver el siguiente).
- `run_kb_ingestion_scheduler.py`: corre `ingest_kb_articles` periódicamente para los pares `(organization_id, subcarpeta)` de `kb_content/ingestion_schedule.yaml` -- modo `--once` (cron del SO) o bucle continuo, mismo patrón que `run_gmail_poller.py`.
- `provision_existing_tenant_llm_keys.py`: aprovisiona virtual keys de LiteLLM para tenants que no la tengan.
- `create_emergency_fallback_key.py` / `create_eval_virtual_key.py`: generan las virtual keys especiales (una vez, a mano).
- `reset_platform_data.py`: limpia datos de la plataforma para volver a empezar en desarrollo.
- `verify_tool_calling.py`: prueba manual del tool-calling end-to-end.

---

## 12. Pendientes conocidos

- **Crédito real en Anthropic/OpenAI**, o al menos aceptar que `EXPENSIVE_MODEL` sigue apuntado a Gemini hasta entonces (ver sección 9).
- **Política de retrasos** (`kb_content/inter_rapidisimo/02-politica-retrasos-investigacion.md`: 5 días hábiles nacional, 3 urbano, +2 rural) — hoy el LLM la aplica leyendo el KB por su cuenta; calcularla en código (contra `MAX(shipment_events.occurred_at)`) es el siguiente paso explícito del roadmap, todavía no implementado.
- **Gap de streaming pre-existente, ya no crítico pero no cerrado del todo**: algunos flujos de error de proveedor en `generate_stream()` no siempre llegan envueltos en `app.llm.errors.LLMError`.
- `ruff`/`mypy`: linting y tipado estático, todavía no instalados ni integrados.
- **`mcp_server/requirements.txt` sigue separado del `requirements.txt` raíz** (distinto Dockerfile, distinto contenedor) pero ya no es verdad que el raíz esté libre de `fastmcp` — ver sección 6.2: el raíz ahora instala `fastmcp-slim` (variante cliente, sin los extras de servidor que sí carga `mcp_server/requirements.txt` del lado del servidor). Para correr `tests/test_mcp_server.py` en local (que ejercita el lado *servidor*) sigue haciendo falta instalar también `mcp_server/requirements.txt`: `pip install -r requirements.txt -r mcp_server/requirements.txt`.
