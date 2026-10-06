# Roadmap

Mejoras de arquitectura recomendadas por el revisor senior, tras el
review del estado actual del proyecto (ver `docs/dependecies.md` para
lo que ya existe hoy). A diferencia de la sección "Pendientes
conocidos" de ese archivo (huecos o decisiones temporales dentro de lo
ya construido), esto es trabajo nuevo: features que todavía no existen
en el proyecto.

Los puntos 2, 3 y 6 ya están implementados (ver las notas en cada
uno); el resto sigue sin implementarse.

---

## 1. Reemplazar function calling por MCP donde ya exista un MCP hecho

Hoy, cada tool que el agente puede llamar (`app/users/tools.py`,
`app/shipments/tools.py`) es una función de Python normal, envuelta en
un `Tool`/`ToolDefinition` (`app/llm/schemas.py`) y ejecutada
directamente en el mismo proceso por `run_tool_loop`/
`run_streaming_tool_loop` (`app/llm/tools.py`). Eso es "function
calling" clásico: el LLM pide llamar una función, la app la ejecuta
en memoria, le devuelve el resultado.

MCP (Model Context Protocol) es un protocolo estándar para exponer
tools/recursos a un agente de LLM, con un servidor separado (por
stdio o HTTP) en vez de funciones Python acopladas al proceso del
agente. La recomendación: para lo que ya tiene un servidor MCP
mantenido por alguien más (ej. un MCP genérico de Gmail, de Postgres,
de Slack), usar ese en vez de reinventar la integración a mano —
menos código propio que mantener, y ya viene probado.

Candidato más claro en este proyecto: la integración de Gmail
(`app/gmail/`) tiene bastante código propio de OAuth y llamadas a la
API que, si existe un MCP de Gmail confiable, podría reemplazarse por
consumir ese servidor en vez de mantener el cliente propio.

## 2. Construir un MCP propio con FastMCP

**Implementado, incluida la migracion del chat.** Ver `mcp_server/`
-- servidor FastMCP (`transport="streamable-http"`) con las 10 tools
de usuarios y envios, como un proxy delgado sobre la API REST
existente (cada tool reenvia el request con el mismo Bearer token
del cliente MCP, en vez de importar SQLAlchemy/los services directo
-- ver el docstring de `mcp_server/server.py` para el detalle del
diseno y el tradeoff que motivo esa decision). Tiene su propio
Dockerfile liviano y servicio en `docker-compose.yml` (`mcp-server`,
puerto 8100), y tests en `tests/test_mcp_server.py`.

El pendiente que quedaba -- migrar el agente de chat para que use
este servidor como cliente MCP en vez de las funciones in-process de
`app/users/tools.py`/`app/shipments/tools.py` -- ya se hizo: esos dos
modulos se borraron, y `ChatService` ahora abre una sesion MCP por
turno (`app.chat.mcp_tools.open_mcp_tools`, ver su docstring) contra
`mcp_server/`, reenviando el Bearer token del usuario que escribio el
mensaje. Las tools que el LLM ve ya no se redeclaran a mano en el
lado del chat -- vienen directo del `list_tools()` que anuncia el
propio servidor MCP, mismo nombre/schema que ya definia cada
`@mcp.tool`.

Consecuencia real, no solo de codigo: `mcp_server/` pasa de ser "un
camino mas, opcional, para otros clientes MCP" a ser una dependencia
dura del chat -- si no esta levantado, ninguna conversacion con
tools funciona. Esto era exactamente el tradeoff que este mismo punto
pedia entender antes de migrar ("un proceso mas que desplegar y
mantener, a cambio de una interfaz estandar..."); se decidio
aceptarlo. Tests en `tests/test_chat_mcp_tools.py`.

Para lo que **no** tiene un MCP existente que sirva — en este
proyecto, la lógica de negocio propia de Inter Rapidísimo
(`app/shipments/tools.py`, y probablemente `app/users/tools.py`) —
construir un servidor MCP propio con [FastMCP](https://github.com/jlowin/fastmcp)
(el framework más usado para esto en Python, decoradores al estilo
FastAPI) en vez de mantenerlas como funciones in-process.

Esto es un cambio de arquitectura real, no solo de sintaxis: hoy las
tools corren en el mismo proceso que el resto de la API; con MCP
pasarían a ser un servicio aparte, y el agente de chat se conectaría a
él como cliente MCP en vez de importar las funciones directamente.
Vale la pena entender bien el tradeoff (un proceso más que desplegar y
mantener, a cambio de una interfaz estándar y reusable desde otros
clientes MCP, no solo este chat) antes de migrar todo de una.

## 3. Pipeline de ingestión de datos para el RAG con varias fuentes

**Implementado.** Ver `app/rag/ingestion/` -- un loader por
formato (`markdown_loader.py`, `pdf_loader.py` via `pypdf`,
`excel_loader.py` via `openpyxl`) que normaliza cualquier fuente a
`IngestedDocument`, y `registry.py` que elige el loader por
extension (`load_documents`/`load_documents_from_dir`). El caso
especial es Excel: una fila de tabla no es prosa, asi que
`ExcelLoader` no usa el chunking por ventanas de palabras de
`chunking.py` -- cada `IngestedDocument` trae su propio `chunks`
(uno por fila, `"columna: valor; columna: valor"`) que
`RAGService.ingest_article` usa tal cual en vez de re-chunkear.
Un archivo de formato no soportado o que falla al leerse (ej. un
PDF escaneado sin capa de texto) se saltea con un warning en vez
de abortar toda la ingesta de la carpeta. Tests en
`tests/test_rag_ingestion.py` y `tests/test_rag_service_ingest.py`.
`RAGService.ingest_article` ahora hace upsert por
`(organization_id, title)` (`app/rag/service.py`), asi que
`scripts/ingest_kb_articles.py` ya es idempotente -- re-correrlo no
duplica articulos, y si un archivo fuente cambio de contenido, la
siguiente corrida actualiza el articulo existente en vez de sumar
uno nuevo.

**La mitad "manual" del pendiente ya se resolvio:** ver
`scripts/run_kb_ingestion_scheduler.py` -- lee pares
`(organization_id, subcarpeta)` desde `kb_content/ingestion_schedule.yaml`
(plantilla en `kb_content/ingestion_schedule.yaml.example`; no se
versiona con valores reales porque los `organization_id` son
especificos de cada deployment) y corre la ingesta de forma
idempotente, en modo `--once` (para que lo invoque un cron del SO,
mismo patron que `scripts/run_gmail_poller.py`) o en bucle continuo
cada `kb_ingestion_poll_interval_seconds` (default 1h). Un error en
una organizacion no frena a las demas. Pendiente real que queda
(la otra mitad, sin resolver): la fuente sigue siendo exclusivamente
una carpeta local (`kb_content/<tenant>/`) -- no hay conectores a
sistemas externos (un Drive, un Confluence, un CMS) todavia.

Hoy la ingesta es manual y de una sola fuente: `scripts/ingest_kb_articles.py`
lee archivos markdown estáticos de `kb_content/inter_rapidisimo/` (8
artículos) y los indexa a mano, corriendo el script tú mismo. No hay
pipeline: no hay múltiples conectores de origen, no hay
programación/triggers, no hay manejo de actualizaciones incrementales
(re-ingestar hoy vuelve a crear todo desde cero).

Construir un pipeline real significa: soportar más de una fuente
(por ejemplo, además de markdown local, un PDF, una hoja de cálculo,
o contenido que venga de un sistema externo), decidir qué pasa cuando
un artículo ya existente cambia (¿se re-embeddea todo el artículo o
solo lo que cambió?), y probablemente un mecanismo para correrlo solo
o programado en vez de invocarlo a mano cada vez.

## 4. Revisar la estrategia de chunking. Chunking adaptativo

El chunking actual (`app/rag/chunking.py`) es deliberadamente simple:
ventanas fijas de 150 palabras con 30 de overlap, sin mirar la
estructura del texto. El propio comentario del archivo ya lo marca
como "el primer candidato a reemplazar" si la KB crece o se vuelve
más heterogénea — este es exactamente ese momento.

Chunking adaptativo significa que el tamaño/límite de cada chunk deja
de ser un número fijo y pasa a depender del contenido: cortar por
encabezados o secciones markdown en vez de a la mitad de una idea,
usar chunks más grandes para texto denso y más pequeños para listas o
tablas, o ajustar el tamaño según qué tan bien se recupera cada uno en
la práctica. Vale la pena tener evidencia real (del punto 6, evaluación
de RAG) de que el chunking actual está fallando en casos concretos
antes de complicarlo — la nota de arriba en `docs/dependecies.md` ya
decía esto mismo: no agregar la complejidad sin evidencia detrás.

## 5. Explorar otros tipos de RAG (estándar, Graph, agéntico)

Hoy solo existe RAG estándar: `RAGService.retrieve` (`app/rag/service.py`)
embeddea la pregunta, trae los `top_k` chunks más parecidos por
distancia coseno, y esos chunks se inyectan en el prompt del sistema
antes de generar — un solo paso de recuperación, siempre el mismo,
sin importar qué tan bien o mal haya salido.

- **RAG estándar** (lo que ya existe): rápido y simple, pero no
  reformula la búsqueda ni encadena varias consultas si la primera no
  trajo lo que hacía falta.
- **GraphRAG**: en vez de (o además de) embeddings, construye un grafo
  de entidades y relaciones a partir del corpus, y responde preguntas
  que requieren conectar varios artículos entre sí (ej. "si un envío
  se pierde Y el cliente ya reclamó antes, ¿qué política aplica?") —
  algo que la búsqueda por similitud simple no captura bien porque
  cada chunk se evalúa aislado del resto.
- **RAG agéntico**: en vez de que la recuperación sea un paso fijo que
  siempre corre antes de generar (como hoy, en `_prepare_turn` de
  `app/chat/service.py`), el propio LLM decide cuándo y qué buscar,
  puede reformular la consulta o pedir una segunda búsqueda si la
  primera no alcanzó. Esto encaja naturalmente con la infraestructura
  de tool-calling que ya existe (`app/llm/tools.py`): retrieval
  pasaría a ser una tool más que el agente decide llamar, en vez de
  algo que corre siempre, sin que el LLM tenga voz en eso.

## 6. Evaluación de RAG (métricas)

**Implementado.** Ver `eval_harness/rag_eval.py` +
`eval_harness/datasets/rag_golden.yaml` -- mismo patrón que
`eval_harness/model_routing_eval.py` (script standalone, no pytest,
porque llama LLMs reales con `LITELLM_EVAL_VIRTUAL_KEY` y consume
presupuesto/tiempo real), aplicado al RAG en vez de al `ModelRouter`.
El dataset son 13 preguntas reales sobre `kb_content/inter_rapidisimo/`
con el/los títulos de artículo esperados (algunas cruzan dos artículos
a propósito, ej. "¿cuándo se declara perdido un envío?" toca tanto
`paquetes_perdidos.md` como `retrasos.md`). Por cada caso: llama a
`RAGService.retrieve` y calcula precisión/recall de retrieval contra
los títulos esperados, después genera una respuesta con el contexto
recuperado y un juez LLM barato (`gemini-3.5-flash-lite`) evalúa si esa
respuesta se sostiene en el contexto o inventa algo que no está ahí
(faithfulness) -- no se trajo RAGAS como dependencia nueva, se
replicaron esas dos métricas a mano con el mismo nivel de esfuerzo que
ya tenía el harness de routing. Requiere que la KB de la organización
pasada como argumento ya esté ingestada:
`python -m scripts.ingest_kb_articles <organization_id> --dir inter_rapidisimo`
seguido de `python -m eval_harness.rag_eval <organization_id>`.

Sin test dedicado, a propósito -- mismo motivo que
`model_routing_eval.py`: no tiene sentido mockear llamadas reales a un
LLM solo para ejercitar el harness, y pytest no es el lugar para algo
que cuesta dinero cada vez que corre.

---

## Orden sugerido (no impuesto por el review)

Los puntos 3-4 (pipeline de ingestión, chunking adaptativo) y 5-6
(tipos de RAG, evaluación) están relacionados: tiene más sentido tener
primero una forma de *medir* qué tan bien funciona el RAG (punto 6,
aunque sea con lo que existe hoy) antes de decidir si vale la pena
complicar el chunking (punto 4) o probar GraphRAG/RAG agéntico (punto
5) — sin esa evidencia, cualquier cambio ahí es una apuesta a ciegas.
Los puntos 1-2 (MCP) son un track aparte, independiente del RAG.

El punto 6 ya está implementado (`eval_harness/rag_eval.py`) -- antes
de tocar el punto 4 o el 5, corré ese harness contra una organización
con la KB real ingestada y mirá si el recall de retrieval o el
faithfulness realmente fallan en casos concretos. El dataset actual
(13 preguntas sobre 8 artículos) es chico a propósito; si la evidencia
que da no alcanza para decidir, el primer paso es ampliarlo, no
todavía complicar el chunking o probar GraphRAG a ciegas.
