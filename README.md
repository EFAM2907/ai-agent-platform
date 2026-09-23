# AI Agent Platform

Plataforma multi-tenant de agente de IA con tool-calling, RAG y gestión de
usuarios/envíos. Proyecto de portafolio para entrevistas Junior/Mid AI
Engineer. Tenant de referencia: **Inter Rapidísimo** (mensajería/logística).

## Qué hace

- Agente de chat conversacional (streaming por SSE) que puede:
  - responder preguntas apoyándose en la knowledge base de la empresa vía RAG (pgvector)
  - consultar y actualizar el estado de envíos/clientes
  - invitar, listar, actualizar, eliminar y cambiar el rol de usuarios de la organización, con exactamente las mismas reglas de autorización que la API REST
  - enrutar automáticamente cada turno entre un modelo barato y uno caro según la complejidad del mensaje
- Aislamiento multi-tenant estricto por organización, con RBAC (`OWNER > ADMIN > MEMBER > VIEWER`) reforzado tanto en la API REST como en las tools de chat.
- Gateway LLM propio (LiteLLM self-hosted) que centraliza ruteo, fallback entre proveedores, reintentos y presupuesto por tenant — la app nunca habla directo con Anthropic/OpenAI/Google.
- Integración con Gmail (OAuth por organización): responde automáticamente preguntas de tracking que lleguen por correo, y envía el correo de bienvenida (rol + contraseña temporal) cuando se invita a un usuario desde el chat.
- Frontend en React con tema oscuro: chat, historial de conversaciones (renombrar/eliminar), gestión de miembros.

## Stack

- **Backend:** FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL/pgvector + Redis + Alembic
- **LLM:** LiteLLM (gateway self-hosted) sobre Gemini/Anthropic/OpenAI, tracing con Langfuse
- **Frontend:** React + TypeScript + Vite
- **Tests:** pytest / pytest-asyncio, contra una base de datos Postgres real de pruebas (no mocks de la capa de datos)
- **Infra:** Docker Compose (Postgres de la app, Postgres de LiteLLM, Redis, LiteLLM)

## Arquitectura (resumen)

Dominios independientes bajo `app/`: `organizations`, `users`, `auth`,
`chat`, `llm`, `rag`, `shipments`, `gmail`. Cada uno con su propio
`models.py`, `repository.py`, `service.py`, `schemas.py`, y (cuando aplica)
`api.py`/`tools.py`. Las reglas de autorización se reaplican explícitamente
en las tools de chat — llamar una tool desde el chat nunca hace nada que el
usuario no pudiera hacer llamando la API REST directo.

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

Levanta Postgres de la app (`5433`), Redis (`6379`) y el gateway LiteLLM
(`4000`) con su propia base de datos.

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
- El correo de bienvenida al crear un usuario hoy solo se envía desde la
  tool de chat `create_user`, no desde el endpoint REST `POST /users/`
  (pendiente decidir si se unifica).
- El poller de Gmail (`scripts/run_gmail_poller.py`) se corre manualmente
  (o vía el Programador de tareas de Windows) en vez de como servicio de
  Docker: una imagen nueva solo para esto pesaría ~400-600MB sin nada que
  la comparta, ya que el backend tampoco está containerizado. El botón
  "Revisar ahora" del panel de Gmail en el frontend cubre poder probar la
  integración sin depender de ese proceso en background.

Lista completa de pendientes y decisiones de diseño:
[`docs/dependecies.md`](docs/dependecies.md#12-pendientes-conocidos).
