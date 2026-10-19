# Roadmap

Estado a octubre de 2026: **la etapa de RAG, trazabilidad, herramientas MCP e integraciones está cerrada**. Este documento deja constancia de qué se hizo con cada punto del roadmap original (incluidos los que se reemplazaron o descartaron y por qué), lo que quedó abierto, y la recomendación para pagos. Para el detalle técnico de lo que existe hoy, ver `docs/dependecies.md`.

---

## 1. Qué pasó con cada punto del roadmap original

| # | Punto original | Decisión | Estado |
|---|---|---|---|
| 1 | Reemplazar function calling por MCP de terceros (ej. Gmail) | **Reemplazado**: se mantiene el código propio y se expone por *nuestro* MCP | Hecho |
| 2 | MCP propio con FastMCP | Hecho y ampliado de 10 a 30 herramientas | Hecho |
| 3 | Pipeline de ingestión multi-fuente | Hecho: md, pdf, xlsx, docx; carpeta, subida por API y Google Drive | Hecho |
| 4 | Chunking adaptativo | **Pospuesto**: sin evidencia que lo pida | Abierto (a demanda) |
| 5a | GraphRAG | **Descartado** | — |
| 5b | RAG agéntico | **Reemplazado por un híbrido acotado**: recuperación automática + herramienta `search_knowledge_base` | Hecho |
| 6 | Evaluación de RAG | Hecho y ampliado (grupos, recall por respuesta, preguntas fuera de la KB) | Hecho |

### 1. Usar un MCP de terceros para Gmail — reemplazado

Los servidores MCP genéricos de Gmail son de **un solo usuario** y no traen lo que este proyecto ya resuelve: verificación del remitente (SPF/DKIM/DMARC más el correo registrado del cliente), idempotencia por correo, topes de respuestas por remitente, plantilla idéntica para guía inexistente y ajena, y aislamiento por organización con el token cifrado. Sustituirlo habría sido perder esas garantías por ahorrar código. **Decisión**: el código propio se queda y se expone por nuestro servidor MCP (`gmail_status`, `list_unresolved_emails`, `poll_gmail_now`). Lo mismo vale para Google Drive.

### 2. MCP propio con FastMCP — hecho, ampliado

`mcp_server/` es un proxy delgado sobre la API REST (cada herramienta reenvía el Bearer token del usuario, así que la autorización vive en un solo lugar). Pasó de 10 a **30 herramientas**: clientes, envíos (alta, historial, estadísticas, demorados), reclamos, base de conocimiento, Gmail y Drive. El chat solo le ofrece al modelo las que el rol del usuario puede usar (`app/chat/tool_access.py`), lo que baja el costo en tokens (≈1.100 para un viewer, ≈3.100 para un admin) y evita que el modelo intente acciones que van a ser rechazadas. Tras cambiar `mcp_server/` hay que reconstruir el contenedor (`docker compose up -d --build mcp-server`).

### 3. Pipeline de ingestión — hecho

Cuatro formatos (`.md`, `.pdf`, `.xlsx`, `.docx`), tres fuentes (carpeta local, subida por `POST /kb/documents`, Google Drive) y un único mecanismo común (`app/rag/sync.py`): indexa lo nuevo, salta lo que no cambió (hash del contenido), y **retira lo que desapareció de la fuente** — antes la ingesta nunca borraba. Un conector nuevo solo tiene que leer y normalizar. Cada documento guarda su origen, versión, modelo de embeddings y nivel de acceso por rol.

### 4. Chunking adaptativo — pospuesto

La evaluación no lo pide: de las tres preguntas que fallan con búsqueda híbrida, ninguna es un problema de cortes. Reconsiderar si, con documentos reales, aparecen preguntas cuya respuesta queda partida entre dos fragmentos.

### 5. Otros tipos de RAG

- **GraphRAG: descartado.** Resuelve preguntas que cruzan muchas entidades en un corpus grande y muy relacionado. Con decenas de documentos, la búsqueda híbrida más un `top_k` razonable ya recupera los artículos cruzados, y el costo de construir y mantener un grafo no tiene evidencia que lo respalde.
- **RAG agéntico: reemplazado por un híbrido acotado.** Hacer *toda* la recuperación una herramienta añade una vuelta de modelo (latencia y costo) a cada pregunta. En su lugar, la recuperación automática de una sola pasada se mantiene, y el modelo puede volver a buscar con otras palabras con `search_knowledge_base` cuando el contexto inicial no alcanza.

### 6. Evaluación de RAG — hecho, ampliado

`python -m eval_harness.rag_eval <organization_id> --compare` mide vectorial contra híbrido, con y sin filtro de relevancia, sin gastar LLM de generación. Resultados con la KB ampliada (31 documentos, 51 preguntas): recall por respuesta **40% → 100%**, recall por título **67% → 94%**, y de 10 preguntas fuera de la base solo 1 devuelve contexto (sin bloquear ninguna legítima). La corrida completa con juez LLM mide *fidelidad*, no corrección.

---

## 2. Lo que se construyó en esta etapa

- **RAG**: búsqueda híbrida con RRF, filtro de relevancia calibrado, índice HNSW, acceso por rol, API de la base de conocimiento, corpus sintético en cuatro formatos.
- **Trazabilidad**: una traza por turno guardada en la base de datos (también cuando falla), `trace_id`/sesión/usuario/tenant enviados al gateway, valoración 👍/👎 y vistas de administración. Antes, Langfuse recibía llamadas sueltas sin tenant ni sesión.
- **Dominio**: reclamos con número, SLA e historial; alta de clientes y envíos con las reglas de la KB; envíos demorados según la política; aviso por correo al cliente con tope diario.
- **Integraciones**: Gmail (ya existía) y Google Drive de solo lectura, ambas por organización.
- **Interfaz**: navegación por vistas, markdown con tablas, estado de herramientas en vivo, detalle de cada respuesta, administración de la base de conocimiento, integraciones y trazabilidad.
- **Correcciones de fondo** encontradas en el camino: una conversación ajena de la misma organización podía continuarse con solo conocer su id; el mensaje del usuario se perdía si el turno fallaba; `ExcelLoader` dejaba el archivo abierto; una falla de embeddings tumbaba el chat entero; la búsqueda no encontraba "julian" para "Julián".

---

## 3. Lo que queda abierto

1. **Validar con documentos y preguntas reales.** Todo el corpus y el dataset de evaluación son sintéticos y los escribimos nosotros; la ventaja medida de la búsqueda híbrida puede ser menor con tráfico real. Es la validación que más pesa.
2. **Tarifas y oficinas por herramienta, no por RAG.** Pedirle al modelo que lea un precio de una fila entre 99 parecidas es frágil. Una herramienta determinista (`cotizar_envio`, `buscar_oficina`) no se equivoca de fila. El RAG sirve para políticas en prosa. Decisión de producto pendiente.
3. **Costo en la traza local.** `cost_usd` queda en `null`; LiteLLM lo calcula y se ve en Langfuse. Cerrarlo exige leer el gasto de LiteLLM por `call id` tras el turno.
4. **Google Drive en un entorno real.** Hay que habilitar la Drive API en el proyecto de Google Cloud, registrar `GOOGLE_DRIVE_REDIRECT_URI` y añadir el scope `drive.readonly` a la pantalla de consentimiento. El flujo OAuth real no se pudo ejecutar sin esas credenciales; el resto está probado con un Drive falso que sirve archivos reales.
5. **Reranker**: no se justifica hoy. Reconsiderar si, con la KB real, fallan preguntas cuyo documento está entre los 20 primeros resultados pero fuera de los 5 que llegan al modelo.
6. **Festivos** en los días hábiles, **OCR** para PDF escaneados y formatos antiguos (`.doc`, `.xls`).
7. **Modelos reales**: `CHEAP_MODEL`/`EXPENSIVE_MODEL` siguen siendo dos `flash-lite` de Gemini mientras Anthropic y OpenAI no tengan crédito.

---

## 4. Pagos — recomendación (para decidir después)

**Recomendación: Wompi** (Bancolombia). Es la pasarela con mejor encaje para una empresa colombiana de logística: tarjetas, PSE, Nequi y Bancolombia, enlaces de pago, checkout alojado (la aplicación nunca toca datos de tarjeta, lo que deja el cumplimiento PCI en su nivel más simple), webhooks firmados, sandbox completo y sin cuota mensual (comisión por transacción). Alternativas razonables: **Mercado Pago** y **PayU**. Conviene verificar condiciones comerciales y disponibilidad vigentes con cada proveedor antes de decidir.

**Casos de uso que tienen sentido aquí**
- Enviar al cliente un **enlace de pago del flete** al admitir un envío.
- Cobrar por adelantado un **cambio de dirección**, una **reexpedición** o una **recolección** (los $4.500 / $3.500 de la KB).
- **Conciliar el recaudo contra entrega** con lo liquidado.

**Cómo encajaría sin refactorizar**
- Una herramienta MCP `create_payment_link(tracking_number, concept)`, con la misma regla de confirmación previa que `notify_shipment_customer`, que reenvía a un endpoint REST nuevo.
- Un endpoint de webhook con verificación de firma e idempotencia (mismo patrón que `claim_message` de Gmail) que actualiza el estado del pago.
- Una tabla `payments` por organización y credenciales por organización, cifradas con `app.core.crypto`, igual que los tokens de Gmail y Drive.
- Solo checkout alojado: no almacenar ni procesar datos de tarjeta.

## Delegación ChatService → MCP server (token exchange, RFC 8693)

**Antes:** el JWT HS256 del usuario se reenviaba tal cual al MCP server, que solo
comprobaba que existiera el header (no validaba firma). Cualquiera con un token
válido podía llamar al MCP directo, y el token filtrado servía en toda la API.

**Ahora:**
- `app/core/sts.py` actúa de STS: intercambia la identidad del usuario por un
  token delegado RS256 (`aud=mcp-server`, `scope` recortado al rol, `act=chat-service`,
  `jti`, TTL de 5 min). JWKS pública en `GET /.well-known/jwks.json`.
- `mcp_server/auth.py` es un resource server: valida firma/iss/aud/exp con la JWKS
  y exige el scope de cada tool (`@tool(mcp, "scope")`). Los scopes viven en
  `app/core/scopes.py` (fuente de verdad); un test verifica que ambos lados coincidan.
- Tools destructivas (`delete_user`, `change_user_role`, `notify_shipment_customer`,
  `poll_gmail_now`, `sync_drive`): el agente solo crea una `PendingAction`; el usuario
  aprueba en la UI (`POST /chat/actions/{id}/approve`, con su sesión normal) y se
  ejecuta con un token de 60 s atado a esos argumentos exactos (`approved_action` +
  `args_hash`). El MCP las rechaza sin ese token.
- La API acepta tokens delegados en rutas de negocio (no en `/chat` ni `/auth`) y
  registra actor/sujeto/jti; `ChatTurnTrace.delegation` guarda lo mismo por turno.

**Límites conocidos / siguientes pasos:** la API REST no impone los scopes del token
delegado por ruta (solo el rol del usuario; el MCP sí impone scopes); sin mTLS /
workload identity entre servicios; emisor propio en vez de un IdP (Keycloak/Auth0) —
el MCP solo depende de `iss`/JWKS/`aud`, así que migrar es cambiar `STS_JWKS_URL` e `STS_ISSUER`.
Requiere `alembic upgrade head` (migración `d1c2b3a4e5f6`).
