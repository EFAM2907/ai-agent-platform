# Deployment guide

> **Status: deployed to Google Cloud Run** (November 2026, trial credits, a
> learning environment rather than a production service). What was run and
> verified there is recorded in [DEPLOY_GCP.md](DEPLOY_GCP.md). The Railway
> path is still **untested**: `railway.json` follows the documented schema
> only, so treat every Railway or AWS step below as a plan to verify.

## 1. Service map

| Service | What it is | Exposure | Talks to |
|---|---|---|---|
| **frontend** | Vite/React static build (Vercel) | Public | api (browser → HTTPS, needs CORS) |
| **api** | FastAPI (`Dockerfile`, port 8000) | Public | db, redis, litellm, mcp-server; fetches nothing from the MCP |
| **mcp-server** | FastMCP proxy (`mcp_server/Dockerfile`, port 8100, path `/mcp`) | **Private** | api (REST calls + JWKS download) |
| **litellm** | LLM gateway (port 4000) | **Private** | litellm-db, LLM providers (internet), Langfuse |
| **db** | PostgreSQL 16 + pgvector (app data, RAG embeddings) | **Private** | — |
| **litellm-db** | PostgreSQL 16 (LiteLLM keys/spend) | **Private** | — |
| **redis** | Redis 7 (rate limiter, fail-open) | **Private** | — |

Call flow for a chat turn: `browser → api → litellm` (LLM) and
`api → mcp-server` (tools, with a short-lived delegated token) →
`mcp-server → api` (REST, same token) → `db`. The MCP validates that token
with the API's JWKS (`<API_BASE_URL>/.well-known/jwks.json`), so the MCP must
reach the API and the API must reach the MCP.

Only the API (and the static frontend) should be reachable from the internet.
The MCP binds to loopback in the local compose; in production keep it on the
private network only.

## 2. Environment variables

"Change in prod" = the local/default value is wrong for production.

### api

| Variable | Purpose / format | Secret | Change in prod |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` (asyncpg driver; Alembic derives a psycopg2 URL from it) | yes | **yes** |
| `ENVIRONMENT` | `production` enables fail-fast validation | no | **yes** (`production`) |
| `SECRET_KEY` | HS256 key for user session JWTs; ≥ 32 chars in production | yes | **yes** |
| `ALGORITHM` | JWT algorithm, `HS256`; required, no default | no | no |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Session lifetime, integer; required, no default | no | review |
| `ENCRYPTION_KEY` | Fernet key (32 bytes url-safe base64, 44 chars) encrypting stored secrets | yes | **yes** |
| `REDIS_URL` | default `redis://localhost:6379/0` | maybe (password) | **yes** |
| `LITELLM_BASE_URL` | default `http://localhost:4000` | no | **yes** (private URL) |
| `LITELLM_MASTER_KEY` | admin key for `/key/generate`; never for chat traffic | yes | **yes** |
| `LITELLM_EMERGENCY_FALLBACK_KEY` | virtual key (created by script, §4); **required**, no default | yes | **yes** |
| `LITELLM_EVAL_VIRTUAL_KEY` | optional, eval harness only | yes | not needed |
| `MCP_SERVER_URL` | default `http://localhost:8100/mcp` | no | **yes** (private URL, keep the `/mcp` path) |
| `STS_PRIVATE_KEY` | RSA PEM (PKCS8, unencrypted). **Required in production** | yes | **yes** |
| `STS_KEY_ID` | `kid` header, default `sts-1` | no | optional |
| `STS_ISSUER` | `iss` claim, default `http://localhost:8000`. **Must equal the MCP's `STS_ISSUER`** | no | **yes** (the API's public https URL) |
| `MCP_AUDIENCE` | `aud` claim, default `mcp-server`. **Must equal the MCP's `MCP_AUDIENCE`** | no | no (keep both equal) |
| `CORS_ORIGINS` | comma-separated; in production only `https://` origins, no `*`. Default `http://localhost:5173` | no | **yes** (the frontend's https URL) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client for Gmail/Drive (optional; without them only the connect flow returns 503) | secret | yes |
| `GOOGLE_REDIRECT_URI` | default `http://localhost:8000/gmail/callback` | no | **yes** (`https://<api>/gmail/callback`) |
| `GOOGLE_DRIVE_REDIRECT_URI` | default `http://localhost:8000/drive/callback` | no | **yes** (`https://<api>/drive/callback`) |
| `LANGFUSE_*` (`API_URL`, `PUBLIC_KEY`, `SECRET_KEY`) | optional tracing | secret | optional |
| `DELEGATED_TOKEN_TTL_SECONDS`, `APPROVED_ACTION_TTL_SECONDS`, `PENDING_ACTION_TTL_MINUTES` | token/approval lifetimes | no | review |
| `EMERGENCY_FALLBACK_DAILY_BUDGET_USD`, `DEFAULT_TENANT_MONTHLY_BUDGET` | budgets | no | review |
| `GMAIL_*`, `KB_INGESTION_*`, `RAG_*` | poller / scheduler / RAG tuning (see `.env.example`) | no | review |

Both Google redirect URIs must **also be registered, exactly equal**, in the
Google Cloud Console OAuth client ("Authorized redirect URIs").

### mcp-server

| Variable | Purpose | Secret | Change in prod |
|---|---|---|---|
| `API_BASE_URL` | the API's private URL (compose passes `MCP_SERVER_API_BASE_URL`); default `http://localhost:8000` in code | no | **yes** |
| `STS_JWKS_URL` | optional override of the JWKS URL; defaults to `$API_BASE_URL/.well-known/jwks.json` | no | only if the API is reached under a different URL than `STS_ISSUER` |
| `STS_ISSUER` | expected `iss`; compared literally to the token claim. Must equal the API's | no | **yes** |
| `MCP_AUDIENCE` | expected `aud`. Must equal the API's | no | keep equal |
| `MCP_SERVER_PORT` | default 8100 | no | platform may inject its own |

`iss` is a plain string comparison: it never needs to be a resolvable URL
inside the MCP container, which is why the JWKS location is a separate setting.

### litellm / databases

`LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `DATABASE_URL` (litellm-db),
provider keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
`DEEPSEEK_API_KEY`), `LANGFUSE_*`; litellm-db: `POSTGRES_USER/PASSWORD/DB`
(`LITELLM_DB_PASSWORD` in compose). All secret; all must be new in prod.

### frontend (build time)

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | the API's public https URL (default `http://localhost:8000`, see `frontend/src/api.ts`). Vite inlines it **at build time**: changing it requires a rebuild/redeploy on Vercel. |

## 3. Generating production secrets

```bash
# SECRET_KEY (>= 32 chars)
python -c "import secrets; print(secrets.token_urlsafe(48))"

# ENCRYPTION_KEY (must be a valid Fernet key: Fernet() fails on anything else)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# STS_PRIVATE_KEY (RSA 2048, PKCS8 PEM, unencrypted)
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out sts.pem

# LITELLM_MASTER_KEY / LITELLM_SALT_KEY
python -c "import secrets; print('sk-' + secrets.token_urlsafe(32))"   # master (sk- prefix)
python -c "import secrets; print('sk-' + secrets.token_urlsafe(32))"   # salt
```

**Multiline PEM in one variable.** `app/core/sts.py` does
`settings.sts_private_key.replace("\\n", "\n")`, so you can either paste the real
multiline PEM (platforms such as Railway accept multiline values) or a
single line with literal `\n` between lines:

```bash
python -c "print(open('sts.pem').read().strip().replace('\n', '\\\\n'))"
```

Store `sts.pem` only in the secret manager, then delete the local file.

**LiteLLM salt key: can it change after first use? No.** LiteLLM derives the
encryption key for stored credentials from `LITELLM_SALT_KEY` (or from the master key
if no salt key is set). Its docs say to set it before adding any models and
never change it; there is no in-place migration, and changing it makes stored
credentials unreadable (they must be re-entered). Rotating the master key is a
different, supported operation and does not touch encrypted data when a salt
key is set. Back the salt key up with the rest of the secrets.
Sources: <https://docs.litellm.ai/docs/proxy/master_key_rotations>,
<https://docs.litellm.ai/docs/proxy/security_encryption_faq>.

## 4. First deployment order

1. **Databases**: `db` (pgvector image; the first migration runs `CREATE EXTENSION vector`, which needs a role allowed to do so), `litellm-db`, `redis`.
2. **LiteLLM** (pinned image in `docker-compose.yml`, mounting `litellm_config.yaml`) with its keys and provider keys. Wait for `/health/readiness`.
3. **Emergency virtual key**: run the existing script against the deployed LiteLLM, from a machine that has the repo (the API image does not ship `scripts/`):
   `LITELLM_BASE_URL=<litellm url> LITELLM_MASTER_KEY=<master> python -m scripts.create_emergency_fallback_key`.
   Importing the settings requires every mandatory variable, including `LITELLM_EMERGENCY_FALLBACK_KEY`: set a temporary placeholder for that one, and use `ENVIRONMENT=development` to skip production validation. Store the printed key as `LITELLM_EMERGENCY_FALLBACK_KEY`.
4. **API**: the entrypoint validates config, runs `alembic upgrade head` under an advisory lock, then starts uvicorn. Healthcheck: `/health`.
5. **MCP server** with `API_BASE_URL` = the API's private URL and the same `STS_ISSUER`/`MCP_AUDIENCE`. Build it from the repo root: `docker build -f mcp_server/Dockerfile .` (the root `.dockerignore` does not exclude `mcp_server/`).
6. **Frontend** on Vercel with `VITE_API_BASE_URL` set to the API's public URL.
7. **Update `CORS_ORIGINS`** on the API with the frontend's final https URL and redeploy it.

## 5. Post-deploy checklist

- [ ] `GET <api>/health/live` → 200; `GET <api>/health` → 200 with `database: ok` (`redis`/`litellm` may be `degraded` without failing the probe).
- [ ] Log in from the frontend (no CORS errors in the browser console).
- [ ] Send a chat message that triggers an MCP tool; check the MCP logs for `tool_call`.
- [ ] From the MCP container, `GET $API_BASE_URL/.well-known/jwks.json` → 200.
- [ ] Gmail/Drive connect flows redirect back correctly (redirect URIs registered in Google Cloud).

## 6. Known limitations

- **`STS_PRIVATE_KEY` must be identical across API replicas.** Without it (development only) each process generates an ephemeral key, so tokens signed by one replica fail validation by the JWKS of another. Production refuses to boot without it. Key rotation has no overlap mechanism: the JWKS publishes one key.
- **Gmail poller and KB scheduler are separate processes**
  (`python -m scripts.run_gmail_poller`, `python -m scripts.run_kb_ingestion_scheduler`). In production they are **worker services**. The current API image copies only `app/`, `migrations/`, `alembic.ini` and `entrypoint.sh`: workers need an image (or a second Dockerfile target) that also includes `scripts/` and `kb_content/` (plus `ingestion_schedule.yaml`, which is not versioned).
- Only one poller instance should run (no distributed lock).
- The pgvector extension and collation version of the managed Postgres must match what the migrations expect (locally the dev DB logs a collation version mismatch warning).

## 7. Platform equivalents

| Piece | Railway | GCP | AWS |
|---|---|---|---|
| API container | Service from `Dockerfile` (`railway.json`) | Cloud Run | ECS Fargate / App Runner |
| MCP server / LiteLLM / workers | Additional services (private) | Cloud Run (internal ingress) / GKE | ECS Fargate (private subnets) |
| Postgres with pgvector | Postgres template using the pgvector image (or self-managed service) | Cloud SQL for PostgreSQL (supports `vector`) | RDS or Aurora PostgreSQL (supports `vector`) |
| Redis | Redis service | Memorystore for Redis | ElastiCache for Redis |
| Secrets | Service variables (sealed) | Secret Manager | Secrets Manager / SSM Parameter Store |
| Image registry | Built by Railway from repo (or external registry) | Artifact Registry | ECR |
| Private network | Private networking (`<service>.railway.internal`) | VPC + Serverless VPC connector / internal ingress | VPC + private subnets + security groups |
| Frontend | Vercel (all cases) | Vercel | Vercel |

## 8. Railway config

`railway.json` (repo root) configures the API service: Dockerfile builder,
healthcheck `/health` with a 300 s timeout (the first start runs migrations),
and restart `ON_FAILURE` up to 5 times. The schema was checked against
<https://docs.railway.com/config-as-code/reference>. **Not tested on a real
deployment.** Note `/health` returns 503 when the database is down, which
keeps a broken release from receiving traffic.
