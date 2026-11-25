# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/). While the version is
`0.x`, anything may still change between releases.

## [0.1.0] - 2026-11-25

First tagged release: a multi-tenant support agent (FastAPI API, LiteLLM
gateway, MCP tool server, RAG on PostgreSQL with pgvector, React frontend)
deployed to Google Cloud Run as a learning environment. The entries below are
the changes made since the previous state of `main`.

### Added

- Google Cloud deployment guide (`docs/DEPLOY_GCP.md`): Cloud Run, Cloud SQL,
  Secret Manager, per-service accounts, Memorystore with Direct VPC egress,
  cost control and teardown.
- `Dockerfile.litellm`: LiteLLM image with the gateway configuration baked in.
- `Dockerfile.jobs`: API image plus `scripts/` and `kb_content/`, to run the
  scripts as Cloud Run Jobs against Cloud SQL.
- `scripts/list_organizations.py`: read-only listing with per-organization
  data counts.
- `scripts/smoke_test_cloud.py`: end-to-end check against a deployed API.
- Sample knowledge base for a second tenant (Conexia).
- `frontend/vercel.json` for the Vercel deployment.

### Changed

- Frontend redesigned as a dark operations console.
- Timeouts and retries are layered, and only LiteLLM retries: 75 s per attempt
  with one retry in LiteLLM; 160 s and no SDK retries in the API. The LiteLLM
  Cloud Run service uses a 170 s request timeout.
- CI also runs on pushes to `dev` and `release/**`.

### Fixed

- The OpenAI SDK retried on its own after 60 s, which discarded valid slow
  answers, duplicated model calls and left requests running in LiteLLM.
- A claim SLA test failed on Mondays and Tuesdays.

### Known limitations

- The API, LiteLLM and MCP services are public URLs protected by their own
  keys and tokens; private networking between them is not set up.
- The MCP server answers `initialize` and `tools/list` without a token. Calling
  a tool does require a valid delegated token.
- With scale-to-zero, the first request after idle time is slow (LiteLLM took
  about 53 s to become ready in one measurement).
- The 75 s timeout is based on only 14 measured successful model calls.
- LiteLLM logs a recurring Prisma error about `budget_limits`; not yet
  investigated.
- Gmail and Drive integrations, the Gmail poller and the knowledge-base
  scheduler were not exercised in the cloud.
