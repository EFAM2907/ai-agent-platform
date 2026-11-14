# AI Agent Platform

Plataforma multi-tenant de agente de IA con tool-calling, RAG y gestión de
usuarios/envíos. Tenant de referencia: **Inter Rapidísimo** (mensajería/logística).

## Qué hace

- Agente de chat conversacional (streaming por SSE) que puede:
  - responder preguntas apoyándose en la knowledge base de la empresa vía RAG (pgvector + búsqueda por palabras clave fusionadas con RRF, con filtro de relevancia y acceso por rol), indexada desde markdown, PDF, Excel o Word, subida por la API o sincronizada desde una carpeta de Google Drive
  - consultar y actualizar el estado de envíos/clientes, dar de alta clientes y envíos, ver los envíos demorados según la política, abrir y seguir reclamos y avisar al cliente por correo
  - invitar, listar, actualizar, eliminar y cambiar el rol de usuarios de la organización, vía el servidor MCP (que reenvía a la API REST con el mismo Bearer token del usuario), con exactamente las mismas reglas de autorización que llamar la API REST directo
  - enrutar automáticamente cada turno entre un modelo barato y uno caro según la complejidad del mensaje
- API REST completa para usuarios y envíos/clientes, y un servidor MCP propio (FastMCP) que expone las mismas operaciones por el protocolo MCP estándar, no solo para este chat — ver [`docs/roadmap.md`](docs/roadmap.md#2-construir-un-mcp-propio-con-fastmcp).
- Aislamiento multi-tenant estricto por organización, con RBAC (`OWNER > ADMIN > MEMBER > VIEWER`) reforzado tanto en la API REST como en las tools de chat.
- Gateway LLM propio (LiteLLM self-hosted) que centraliza ruteo, fallback entre proveedores, reintentos y presupuesto por tenant — la app nunca habla directo con Anthropic/OpenAI/Google.
- Integración con Gmail (OAuth por organización): responde automáticamente preguntas de tracking que lleguen por correo, y envía el correo de bienvenida (rol + contraseña temporal) cuando se invita a un usuario desde el chat.
- Trazabilidad por turno: cada respuesta guarda qué modelo la generó, qué fragmentos de la base de conocimiento usó, qué herramientas ejecutó y cuánto tardó, con valoración 👍/👎 del usuario; los administradores ven el listado y las estadísticas.
- Frontend en React con tema oscuro: chat (fuentes, estado de herramientas en vivo, detalle de cada respuesta), historial de conversaciones, y para administradores la base de conocimiento, las integraciones (Gmail, Google Drive) y la trazabilidad.

## Stack

- **Backend:** FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL/pgvector + Redis + Alembic
- **LLM:** LiteLLM (gateway self-hosted) sobre Gemini/Anthropic/OpenAI/DeepSeek, tracing con Langfuse
- **Frontend:** React + TypeScript + Vite
- **Tests:** pytest / pytest-asyncio, contra una base de datos Postgres real de pruebas (no mocks de la capa de datos)
- **Infra:** Docker Compose (Postgres de la app, Postgres de LiteLLM, Redis, LiteLLM, servidor MCP)

## Arquitectura (resumen)

Dominios independientes bajo `app/`: `organizations`, `users`, `auth`,
`chat`, `llm`, `rag`, `shipments`, `claims`, `gmail`, `drive`. Cada uno con su propio
`models.py`, `repository.py`, `service.py`, `schemas.py`, y (cuando aplica)
`api.py`. Las tools de usuarios/envíos que el chat ofrece al LLM
(`app/chat/mcp_tools.py`) ya no son función calling in-process: son un
cliente MCP contra `mcp_server/`, que a su vez reenvía cada llamada a la
API REST de ese mismo dominio con el Bearer token del usuario — un único
lugar (la API REST) decide qué puede hacer cada rol, nunca duplicado en el
lado del chat.

`mcp_server/` es un paquete aparte, fuera de `app/` a propósito: un servicio
independiente (proxy delgado sobre la API REST, ver sección 6.2 de
`docs/dependecies.md`), con su propio `Dockerfile` y `requirements.txt` —
nunca importa nada de `app/`. Es una dependencia dura del chat: sin
`mcp-server` levantado, ninguna conversación con tools funciona.

Documentación detallada de cada librería, por qué se eligió y cómo se usa
concretamente: [`docs/dependecies.md`](docs/dependecies.md).

## Cómo correrlo

### Requisitos

- Python 3.11+
- Node 18+ (frontend)
- Docker + Docker Compose

### 1. Variables de entorno

```bash
cp .env.example .env
```

Completar según los comentarios de cada variable en `.env.example`
(clave de cifrado, credenciales de LiteLLM y, opcionalmente, OAuth de
Google para Gmail).

### 2. Infraestructura

```bash
docker compose up -d
```

Levanta Postgres de la app (`5433`), Redis (`6379`), el gateway LiteLLM
(`4000`) con su propia base de datos, y el servidor MCP (`8100` — necesita
la API principal corriendo en el host, no en Docker; ver sección 6.2 de
`docs/dependecies.md` si corre en Linux sin Docker Desktop).

### 3. Migraciones

```bash
alembic upgrade head
```

### 4. Backend

```bash
uvicorn app.main:app --reload
```

API en `http://localhost:8000`, docs interactivas en `/docs`.

### 5. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI en `http://localhost:5173`.

### 6. Tests

```bash
pytest
```

## Estado actual / decisiones conocidas

- `EXPENSIVE_MODEL` está temporalmente apuntado a un modelo Gemini gratuito
  en vez de un modelo de pago, porque Anthropic/OpenAI todavía no tienen
  crédito real — marcado explícitamente como `# TEMPORAL` en
  `app/chat/service.py` y `litellm_config.yaml`.
- El poller de Gmail (`scripts/run_gmail_poller.py`) se corre manualmente
  (o vía el Programador de tareas de Windows) en vez de como servicio de
  Docker: una imagen nueva solo para esto pesaría ~400-600MB sin nada que
  la comparta, ya que el backend tampoco está containerizado. El botón
  "Revisar ahora" del panel de Gmail en el frontend cubre poder probar la
  integración sin depender de ese proceso en background.
- La API principal se queda corriendo con `uvicorn` directo en el host,
  no como contenedor, aunque el resto de la infraestructura (Postgres,
  Redis, LiteLLM, el servidor MCP) sí está en `docker-compose.yml` —
  decisión explícita, no pendiente: Docker Desktop en esta máquina tiene
  poco margen de RAM asignada (~3.2 de 4.2 GB ya en uso solo con esos 5
  servicios), y conviene guardar ese margen para una imagen que haga
  falta más adelante en vez de gastarlo en algo que ya funciona bien con
  `uvicorn --reload`.

Lista completa de pendientes y decisiones de diseño:
[`docs/dependecies.md`](docs/dependecies.md#12-pendientes-conocidos).

Mejoras de arquitectura recomendadas en el review con el senior (tres ya
implementadas — MCP propio con el chat migrado a usarlo, pipeline de
ingestión multi-formato programable, y evaluación de RAG — el resto no
todavía): [`docs/roadmap.md`](docs/roadmap.md).
