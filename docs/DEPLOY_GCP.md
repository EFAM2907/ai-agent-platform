# Despliegue en Google Cloud (Cloud Run) — opción B

> **Estado: plan sin probar.** Ningún comando de este documento se ha
> ejecutado contra un proyecto real. Los puntos marcados con **[verificar]**
> son supuestos razonables que hay que confirmar en el primer despliegue.
> Complementa a [DEPLOY.md](DEPLOY.md) (mapa de servicios y variables) y no
> lo repite: las variables obligatorias de la API están en su §2.

## Decisiones de este plan

- **Opción B (sin red privada):** `api`, `litellm` y `mcp-server` son
  servicios de Cloud Run con URL pública. LiteLLM se protege con su master
  key / virtual keys y el MCP valida el JWT del STS con el JWKS de la API.
  Es más simple que una VPC con ingress interno, pero **expone más
  superficie**. La opción A (ingress interno + Direct VPC egress) queda como
  segunda vuelta.
- **Sin Redis al inicio.** `DEPLOY.md` indica que es un rate limiter
  *fail-open*. **[verificar]** que la API arranca y responde sin `REDIS_URL`
  (`/health` debe dar `redis: degraded`, no 503).
- **Una instancia de Cloud SQL con dos bases** (`ai_agent_platform` y
  `litellm`) en lugar de dos instancias. Se pierde el aislamiento que
  describe el compose; ahorra una instancia que cobra por hora.
- **Escala a cero** (`--min-instances 0`). El primer request tras inactividad
  paga un arranque en frío; LiteLLM es el más lento (decenas de segundos).
- **LLM:** Gemini por API key, como hoy. Vertex AI queda fuera: el crédito de
  prueba no aplica a la plataforma Gemini.

## Qué cobra y qué no

| Recurso | Cobra mientras está creado |
|---|---|
| Cloud Run (escala a cero) | No, solo por uso real |
| Artifact Registry | Poco: almacenamiento de imágenes |
| Secret Manager | Centavos |
| **Cloud SQL** | **Sí, por hora, aunque nadie lo use** |
| Tokens de Gemini (API key) | Fuera de Google, en tu cuenta de AI Studio |

## 0. Variables de trabajo

PowerShell:

```powershell
$PROJECT_ID = "tu-id-de-proyecto"
$REGION     = "us-central1"
$REPO       = "ai-agent"
$SQL        = "ai-agent-pg"
gcloud config set project $PROJECT_ID
$REGISTRY   = "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO"
```

## 1. Activar APIs

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com sqladmin.googleapis.com
```

## 2. Registro de imágenes

```powershell
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
gcloud auth configure-docker "$REGION-docker.pkg.dev"
```

## 3. Cloud SQL (pgvector)

```powershell
gcloud sql instances create $SQL --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-f1-micro --region=$REGION --storage-size=10GB
gcloud sql databases create ai_agent_platform --instance=$SQL
gcloud sql databases create litellm --instance=$SQL
gcloud sql users create appuser --instance=$SQL --password="<GENERA-UNA-CONTRASENA-LARGA>"
```

- `db-f1-micro` es la instancia más barata (núcleo compartido, sin SLA).
  Suficiente para aprender; la creación de índices HNSW puede ir lenta.
- La primera migración ejecuta `CREATE EXTENSION vector`. Un usuario creado
  con `gcloud sql users create` pertenece a `cloudsqlsuperuser`, que puede
  hacerlo. **[verificar]**.
- Nombre de conexión (lo necesitas abajo):
  `gcloud sql instances describe $SQL --format="value(connectionName)"`

### URLs de conexión por socket de Cloud SQL

Cloud Run se conecta con `--add-cloudsql-instances` y un socket Unix en
`/cloudsql/<connectionName>`:

```text
API:     postgresql+asyncpg://appuser:<PASS>@/ai_agent_platform?host=/cloudsql/<connectionName>
LiteLLM: postgresql://appuser:<PASS>@localhost/litellm?host=/cloudsql/<connectionName>
```

**[verificar]** que Alembic deriva bien la URL psycopg2 a partir de la
asyncpg con el parámetro `host=` (DEPLOY.md §2 dice que la deriva), y que
Prisma (LiteLLM) acepta el socket. Si alguna falla, el plan B es habilitar
IP pública en Cloud SQL con red autorizada, solo para depurar.

## 4. Cuentas de servicio (mínimo privilegio)

Una por servicio, para que cada uno tenga solo lo que necesita:

```powershell
foreach ($sa in "api-sa","litellm-sa","mcp-sa") {
  gcloud iam service-accounts create $sa
}
$P = "serviceAccount:{0}@$PROJECT_ID.iam.gserviceaccount.com"
foreach ($sa in "api-sa","litellm-sa") {
  gcloud projects add-iam-policy-binding $PROJECT_ID --member=($P -f $sa) --role=roles/secretmanager.secretAccessor
  gcloud projects add-iam-policy-binding $PROJECT_ID --member=($P -f $sa) --role=roles/cloudsql.client
}
# mcp-sa no necesita secretos ni base de datos: sin roles extra.
```

> `secretAccessor` a nivel de proyecto es lo cómodo para aprender. Más
> estricto: conceder el rol por secreto (`gcloud secrets add-iam-policy-binding`).

## 5. Secretos

Genera los valores con los comandos de DEPLOY.md §3 y guárdalos así (el valor
nunca queda en el historial si lo lees de un archivo o de la entrada):

```powershell
# Ejemplo: una linea por secreto; repite con cada nombre de la lista.
"valor" | gcloud secrets create NOMBRE --data-file=-
```

| Secreto | Lo usa |
|---|---|
| `DATABASE_URL_API` | api |
| `SECRET_KEY`, `ENCRYPTION_KEY`, `STS_PRIVATE_KEY` | api |
| `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` | litellm (y master en el script de la llave de emergencia) |
| `LITELLM_DATABASE_URL` | litellm |
| `GEMINI_API_KEY` | litellm |
| `LITELLM_EMERGENCY_FALLBACK_KEY` | api (se crea en el paso 8) |

`LITELLM_SALT_KEY` **no puede cambiar después del primer uso** (DEPLOY.md §3).
Guárdalo también fuera de GCP.

`litellm_config.yaml` declara `callbacks: ["langfuse_otel"]` y entradas de
OpenAI, Anthropic y DeepSeek. **[verificar]** si LiteLLM arranca sin las
variables `LANGFUSE_*` y sin esas keys; si no, hay que quitar el callback o
definirlas vacías, y los modelos sin key simplemente fallarán al usarse.

## 6. Construir y subir las imágenes

Desde la raíz del repo:

```powershell
docker build -t "$REGISTRY/api:v1" .
docker build -f mcp_server/Dockerfile -t "$REGISTRY/mcp:v1" .
docker build -f Dockerfile.litellm -t "$REGISTRY/litellm:v1" .
docker push "$REGISTRY/api:v1"
docker push "$REGISTRY/mcp:v1"
docker push "$REGISTRY/litellm:v1"
```

Si construyes desde una máquina Apple Silicon añade
`--platform linux/amd64`.

## 7. Desplegar LiteLLM

```powershell
$CONN = gcloud sql instances describe $SQL --format="value(connectionName)"
gcloud run deploy litellm `
  --image "$REGISTRY/litellm:v1" --region $REGION `
  --service-account "litellm-sa@$PROJECT_ID.iam.gserviceaccount.com" `
  --port 4000 --memory 2Gi --cpu 1 --cpu-boost `
  --min-instances 0 --max-instances 1 `
  --add-cloudsql-instances $CONN `
  --set-secrets "LITELLM_MASTER_KEY=LITELLM_MASTER_KEY:latest,LITELLM_SALT_KEY=LITELLM_SALT_KEY:latest,DATABASE_URL=LITELLM_DATABASE_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest" `
  --allow-unauthenticated
```

`--allow-unauthenticated` es la opción B: la protección real es la master
key. Comprueba `https://<url-litellm>/health/readiness`.

> **Verificado en el primer despliegue real:** con 2 GiB LiteLLM arrancó, se
> conectó a Cloud SQL por socket (Prisma acepta la URL con `host=`), aplicó
> sus 115 migraciones y `/health/readiness` devolvió `db: connected`. Sin
> llave rechaza con 401. Con la master key llegó a Gemini. **Ojo con
> `max_tokens` bajos:** Gemini gasta tokens en "razonar" y con 20 devolvió
> el texto vacío; con 500 respondió bien (127 de 129 tokens fueron de
> razonamiento).

## 8. Llave virtual de emergencia

Igual que DEPLOY.md §4.3, desde una máquina con el repo:

```powershell
$env:LITELLM_BASE_URL = "https://<url-litellm>"
$env:LITELLM_MASTER_KEY = "<master>"
python -m scripts.create_emergency_fallback_key
```

Guarda la llave que imprime como secreto `LITELLM_EMERGENCY_FALLBACK_KEY`.

## 9. Desplegar la API

El `STS_ISSUER` debe ser la URL pública de la propia API, que solo se conoce
tras el primer despliegue: se despliega y luego se actualiza.

```powershell
gcloud run deploy api `
  --image "$REGISTRY/api:v1" --region $REGION `
  --service-account "api-sa@$PROJECT_ID.iam.gserviceaccount.com" `
  --port 8000 --memory 1Gi --cpu 1 --cpu-boost `
  --min-instances 0 --max-instances 2 `
  --add-cloudsql-instances $CONN `
  --set-secrets "DATABASE_URL=DATABASE_URL_API:latest,SECRET_KEY=SECRET_KEY:latest,ENCRYPTION_KEY=ENCRYPTION_KEY:latest,STS_PRIVATE_KEY=STS_PRIVATE_KEY:latest,LITELLM_MASTER_KEY=LITELLM_MASTER_KEY:latest,LITELLM_EMERGENCY_FALLBACK_KEY=LITELLM_EMERGENCY_FALLBACK_KEY:latest" `
  --set-env-vars "ENVIRONMENT=production,ALGORITHM=HS256,ACCESS_TOKEN_EXPIRE_MINUTES=60,LITELLM_BASE_URL=https://<url-litellm>,CORS_ORIGINS=https://<tu-frontend>.vercel.app" `
  --allow-unauthenticated

$API_URL = gcloud run services describe api --region $REGION --format="value(status.url)"
gcloud run services update api --region $REGION --update-env-vars "STS_ISSUER=$API_URL,GOOGLE_REDIRECT_URI=$API_URL/gmail/callback,GOOGLE_DRIVE_REDIRECT_URI=$API_URL/drive/callback"
```

Añade el resto de variables obligatorias de DEPLOY.md §2 que falten
(`ACCESS_TOKEN_EXPIRE_MINUTES`, presupuestos, etc.). El arranque falla rápido
(`entrypoint.sh` valida la configuración) y el mensaje dice qué falta.

Las migraciones corren dentro del arranque. Cloud Run espera por defecto 240 s
a que el contenedor escuche; con `--cpu-boost` debería alcanzar, pero **[verificar]**
en el primer despliegue con la base vacía.

> **Verificado en el primer despliegue real:** arrancó en ~20 s con la base
> vacía, aplicó las 22 migraciones (incluida la de `pgvector` con el índice
> HNSW y `unaccent`) con `appuser`, y `/health` devolvió
> `database: ok, litellm: ok, redis: degraded`. Es decir: la API arranca
> sin Redis, Alembic deriva bien la URL del socket de Cloud SQL, y
> `CREATE EXTENSION vector` funciona con ese usuario. El JWKS
> (`/.well-known/jwks.json`) responde 200 con una clave RSA (`kid: sts-1`).
>
> Cloud Run da a cada servicio **dos URLs equivalentes**
> (`api-<número>.us-central1.run.app` y `api-<hash>-uc.a.run.app`).
> `STS_ISSUER` se compara como texto, así que hay que usar **siempre la
> misma forma** en la API y en el MCP: aquí se usó la de `<número>`.

## 10. Desplegar el servidor MCP

```powershell
gcloud run deploy mcp-server `
  --image "$REGISTRY/mcp:v1" --region $REGION `
  --service-account "mcp-sa@$PROJECT_ID.iam.gserviceaccount.com" `
  --port 8100 --memory 256Mi --cpu 1 `
  --min-instances 0 --max-instances 1 `
  --set-env-vars "API_BASE_URL=$API_URL,STS_ISSUER=$API_URL,MCP_AUDIENCE=mcp-server,MCP_SERVER_PORT=8100" `
  --allow-unauthenticated

$MCP_URL = gcloud run services describe mcp-server --region $REGION --format="value(status.url)"
gcloud run services update api --region $REGION --update-env-vars "MCP_SERVER_URL=$MCP_URL/mcp"
```

`MCP_AUDIENCE` y `STS_ISSUER` deben ser idénticos en la API y el MCP
(DEPLOY.md §2). El servidor escucha en `0.0.0.0` (`mcp_server/server.py`), así
que Cloud Run puede alcanzarlo.

> **Verificado en el primer despliegue real:** el servidor acepta el `Host`
> de `*.run.app` (responde `initialize` con 200). **La autenticación no está
> en la entrada sino en cada herramienta** (`mcp_server/auth.py`, decorador
> `@tool`): `initialize` y `tools/list` responden **sin token** (se ven los
> nombres y descripciones de las 30 herramientas), pero **llamar** a una
> herramienta sin token o con un token falso devuelve error. Pendiente de
> probar: una llamada con un **token delegado real** emitido por la API
> (valida firma con el JWKS, `iss` y `aud`), que requiere iniciar sesión
> en la API.

## 11. Frontend y CORS

En Vercel: `VITE_API_BASE_URL=$API_URL` y redespliega (Vite lo incrusta en
build). Luego pon la URL final de Vercel en `CORS_ORIGINS` de la API.

### 11b. Redis (Memorystore) para el limitador de peticiones

Sin Redis la API funciona (el limitador es *fail-open*), pero **no limita
intentos de login ni de registro** y cada petición deja un traceback
`ConnectionRefusedError ... 6379` en los logs. Memorystore solo tiene IP
privada, por eso la API necesita salida por la VPC (Direct VPC egress):

```powershell
gcloud services enable compute.googleapis.com redis.googleapis.com   # crea la red "default"
gcloud redis instances create ai-agent-redis --region=$REGION --tier=basic --size=1 --network=default --redis-version=redis_7_2
$REDIS_IP = gcloud redis instances describe ai-agent-redis --region=$REGION --format="value(host)"
gcloud run services update api --region $REGION --network=default --subnet=default --vpc-egress=private-ranges-only --update-env-vars "REDIS_URL=redis://${REDIS_IP}:6379/0"
```

- **Cobra por hora** mientras exista (nivel básico de 1 GB: del orden de
  35 USD/mes, cifra por verificar). Se borra con
  `gcloud redis instances delete ai-agent-redis --region $REGION`.
- `--vpc-egress=private-ranges-only` manda por la VPC solo el tráfico a IPs
  privadas (Redis); las llamadas a LiteLLM y al MCP siguen saliendo por su
  URL pública.
- La IP de Redis (`10.89.x.x`) no está en la subred `10.128.0.0/20`: viene
  de un rango reservado para servicios gestionados, y aun así lo cubre
  `private-ranges-only`.

> **Verificado:** tras conectarlo, `/health` dio `redis: ok`, no quedaron
> tracebacks de Redis y el limitador de login cuenta (el 6.º intento
> fallido seguido devuelve `429` con `Retry-After: 60`).
>
> **Ojo con `litellm: degraded` en `/health`:** la comprobación de salud da
> solo 2 s a LiteLLM (`CHECK_TIMEOUT_SECONDS` en `app/health/api.py`). Con
> escala a cero, la primera consulta tras un rato inactivo cae en el
> arranque en frío y sale `degraded`; con LiteLLM despierto sale `ok`. Solo
> la base de datos decide el código HTTP, así que no es un fallo.

### 11c. Cargar datos de ejemplo con un Cloud Run Job

Los scripts de `scripts/` necesitan alcanzar la base de Cloud SQL. En vez de
descargar el Auth Proxy o abrir la red de la instancia, se ejecutan **dentro
de GCP** como un *Cloud Run Job*, conectado por el mismo socket que la API.
La imagen de la API no lleva `scripts/` ni `kb_content/`, así que se usa una
imagen derivada (`Dockerfile.jobs`, con su `Dockerfile.jobs.dockerignore`):

```powershell
docker build -f Dockerfile.jobs --build-arg BASE_IMAGE="$REGISTRY/api:v1" -t "$REGISTRY/jobs:v1" .
docker push "$REGISTRY/jobs:v1"      # si da timeout, repetir: reanuda donde quedó

gcloud run jobs create ai-agent-tools --image "$REGISTRY/jobs:v1" --region $REGION `
  --service-account "api-sa@$PROJECT_ID.iam.gserviceaccount.com" `
  --memory 1Gi --max-retries 0 --task-timeout 1800 --set-cloudsql-instances $CONN `
  --command python --args="-m,scripts.list_organizations" `
  --set-secrets "DATABASE_URL=DATABASE_URL_API:latest,SECRET_KEY=SECRET_KEY:latest,ENCRYPTION_KEY=ENCRYPTION_KEY:latest,STS_PRIVATE_KEY=STS_PRIVATE_KEY:latest,LITELLM_MASTER_KEY=LITELLM_MASTER_KEY:latest,LITELLM_EMERGENCY_FALLBACK_KEY=LITELLM_EMERGENCY_FALLBACK_KEY:latest" `
  --set-env-vars "ENVIRONMENT=production,ALGORITHM=HS256,ACCESS_TOKEN_EXPIRE_MINUTES=60,LITELLM_BASE_URL=<url-litellm>,CORS_ORIGINS=<url-frontend>"

# Ejecutar con otros argumentos (cada ejecución arranca un contenedor nuevo):
gcloud run jobs execute ai-agent-tools --region $REGION --wait --args="-m,scripts.list_organizations"
gcloud run jobs execute ai-agent-tools --region $REGION --wait --args="-m,scripts.seed_inter_rapidisimo_shipments,<org_id>"
gcloud run jobs execute ai-agent-tools --region $REGION --wait --args="-m,scripts.ingest_kb_articles,<org_id>,--dir,inter_rapidisimo"
```

`scripts.list_organizations` es de solo lectura: muestra el id de cada
organización y los conteos (KB, clientes, envíos). La salida del job se lee
en Cloud Logging (`resource.type=cloud_run_job`).

> **Verificado:** 6 clientes y 15 envíos sembrados; 31 documentos de
> `kb_content/inter_rapidisimo/` indexados (266 chunks). La ingesta tardó
> ~5 min porque la cuota de embeddings de Gemini se agotó dos veces y el
> script esperó 65 s cada vez (`rate_limit_attempts`), sin fallar. Cada
> ejecución del job tarda 1-2 min solo en arrancar. El seed **no es
> idempotente** (ver su docstring): no repetirlo sobre la misma organización.

## 12. Comprobaciones

Las de DEPLOY.md §5, con las URLs de `run.app`. Primero:

```powershell
curl "$API_URL/health/live"
curl "$API_URL/health"
```

## 13. Controlar el gasto

- Alerta de presupuesto creada en Billing (alertas, no frenos: Google tarda
  hasta 24 h en registrar costos).
- **Detener Cloud SQL sin borrarlo** (deja de cobrar CPU; el disco sigue
  cobrando poco):

  ```powershell
  gcloud sql instances patch $SQL --activation-policy=NEVER    # apagar
  gcloud sql instances patch $SQL --activation-policy=ALWAYS   # encender
  ```

- **Desmontar todo:**

  ```powershell
  gcloud run services delete mcp-server --region $REGION
  gcloud run services delete api --region $REGION
  gcloud run services delete litellm --region $REGION
  gcloud redis instances delete ai-agent-redis --region $REGION
  gcloud sql instances delete $SQL
  ```

  O borrar el proyecto entero (queda 30 días en "pendiente de eliminación").

## Segunda vuelta: pasar a red privada (opción A)

Pasar `litellm` y `mcp-server` a `--ingress internal`, dar a `api` y a
`mcp-server` salida por Direct VPC egress hacia la VPC y quitar
`--allow-unauthenticated` de esos dos. Es buen ejercicio de IAM y redes, pero
añade configuración de subred y de salida que no hace falta para ver la app
funcionando.
