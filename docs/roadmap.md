# Roadmap

Mejoras de arquitectura recomendadas por el revisor senior, tras el
review del estado actual del proyecto (ver `docs/dependecies.md` para
lo que ya existe hoy). A diferencia de la sección "Pendientes
conocidos" de ese archivo (huecos o decisiones temporales dentro de lo
ya construido), esto es trabajo nuevo: features que todavía no existen
en el proyecto.

Ninguno de estos está implementado todavía.

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

Hoy existe evaluación para el `ModelRouter` (`eval_harness/model_routing_eval.py`
+ `eval_harness/datasets/model_routing_golden.yaml`: mide qué tan bien
clasifica barato/caro contra un dataset etiquetado a mano) pero nada
equivalente para el RAG — no hay forma de saber, con números, si el
sistema recupera los chunks correctos o si las respuestas realmente
se basan en lo recuperado.

Construir esto significa un dataset propio de preguntas con la
respuesta/fuente correcta esperada (mismo patrón que ya existe para el
model routing, pero para la KB), y medir cosas como: si los chunks
recuperados son los relevantes para esa pregunta (precisión/recall de
retrieval), y si la respuesta generada se sostiene en lo que se
recuperó o inventa cosas que no están ahí (faithfulness/groundedness).
Frameworks como [RAGAS](https://github.com/explodinggradients/ragas)
ya traen estas métricas implementadas, en vez de tener que calcularlas
todas desde cero.

---

## Orden sugerido (no impuesto por el review)

Los puntos 3-4 (pipeline de ingestión, chunking adaptativo) y 5-6
(tipos de RAG, evaluación) están relacionados: tiene más sentido tener
primero una forma de *medir* qué tan bien funciona el RAG (punto 6,
aunque sea con lo que existe hoy) antes de decidir si vale la pena
complicar el chunking (punto 4) o probar GraphRAG/RAG agéntico (punto
5) — sin esa evidencia, cualquier cambio ahí es una apuesta a ciegas.
Los puntos 1-2 (MCP) son un track aparte, independiente del RAG.
