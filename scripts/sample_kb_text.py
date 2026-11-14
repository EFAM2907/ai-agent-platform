"""Contenido de prosa del corpus SINTETICO de prueba (md, pdf, docx) que
genera scripts/generate_sample_kb.py.

TODO lo de este archivo es inventado: tarifas, codigos, telefonos,
resoluciones, plazos. Sirve para probar el pipeline de ingestion y la
busqueda hibrida del RAG, NO es informacion real de Inter Rapidisimo y
no debe usarse con clientes. Cada documento generado lo dice en su pie.

Los datos exactos (codigos, plazos, montos) estan puestos a proposito:
eval_harness/datasets/rag_golden.yaml hace preguntas sobre ellos. Si se
cambia uno aqui, hay que cambiar la pregunta que lo usa.
"""

from __future__ import annotations

SYNTHETIC_NOTICE = (
    "Documento sintético de prueba: contenido inventado para evaluar la "
    "búsqueda del RAG, no es información real de la empresa."
)

# ---------------------------------------------------------------------------
# Markdown: nombre de archivo -> contenido. El titulo del articulo es el
# primer encabezado (ver MarkdownLoader).
# ---------------------------------------------------------------------------
MARKDOWN_DOCS: dict[str, str] = {
    "09-cambio-direccion-entrega.md": """# Cambio de Dirección de Entrega

El remitente o el destinatario pueden pedir que un envío se entregue en una dirección distinta a la registrada, siempre que el envío **todavía no haya salido a reparto**. Si el estado ya es "en ruta de entrega", el cambio no es posible y se debe esperar al primer intento de entrega o a una novedad.

**Reglas del cambio:**

- Se permite un único cambio de dirección por envío.
- Dentro de la misma ciudad tiene un costo fijo de $4.500, que se cobra a quien lo solicita.
- Si la nueva dirección está en otra ciudad no es un cambio de dirección sino una reexpedición, con las condiciones de `devoluciones-reexpedicion`.
- La solicitud debe hacerla el titular de la cuenta o el destinatario verificado; un tercero no puede pedirla.

**Cómo solicitarlo:** por la línea de servicio (601) 555-0142 o desde el seguimiento del envío en la página web. Quedan registrados el número de guía, la dirección anterior, la nueva y quién la pidió.

Un cambio de dirección no reinicia el tiempo de entrega prometido, pero si la nueva zona es rural se aplica el recargo de zona de difícil acceso.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "10-devoluciones-reexpedicion.md": """# Devoluciones y Reexpedición de Envíos

Un envío vuelve al remitente cuando no se puede entregar o cuando el destinatario lo rechaza.

**Intentos de entrega:** el mensajero hace hasta 3 intentos de entrega en días hábiles distintos. Cada intento fallido registra una novedad; el caso más frecuente es el código N-05 (destinatario ausente). Después del tercer intento fallido el envío pasa a la oficina de destino.

**Custodia:** el paquete permanece 15 días calendario en la oficina de destino esperando al destinatario. Vencido ese plazo se inicia la devolución al remitente.

**Costo de la devolución:** el retorno cuesta el 70% de la tarifa original del envío y lo paga el remitente, salvo que la causa sea un error de Inter Rapidísimo (dirección mal transcrita, por ejemplo), caso en que no se cobra.

**Reexpedición:** si el destinatario pide recibirlo en otra ciudad, el envío se reexpide con una tarifa nueva calculada desde la ciudad donde se encuentra, y el tiempo de entrega vuelve a contar desde cero.

El remitente recibe una notificación en cada paso: tercer intento fallido, inicio de custodia y salida de la devolución.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "11-entrega-zonas-rurales.md": """# Entrega en Zonas Rurales y de Difícil Acceso

Se consideran zonas de difícil acceso los municipios y veredas sin vía carreteable permanente, los que dependen de transporte fluvial o aéreo, y los que figuran como "Ocurre" o "Aliado" en la cobertura de municipios.

**Condiciones comunes:**

- Recargo R-01 del 18% sobre el valor del flete.
- Se suman 2 días hábiles al tiempo de entrega estimado del servicio Estándar.
- El servicio Express no está disponible; solo se ofrece Estándar.

**Entrega por vía aérea:** Mitú, Inírida y Puerto Carreño reciben carga una vez por semana, los miércoles. Un envío admitido el jueves espera hasta el miércoles siguiente para salir, y esa espera ya está incluida en el tiempo estimado que se le informa al cliente.

**Entrega en oficina ("Ocurre"):** en algunos municipios no hay mensajero. El paquete se deja en la oficina o punto aliado y el destinatario debe reclamarlo con su documento de identidad dentro de 10 días calendario.

El remitente debe ser informado del recargo y del tiempo adicional **antes** de admitir el envío, no después.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "12-seguro-valor-declarado.md": """# Seguro de Envío y Valor Declarado

El valor declarado es el precio del contenido que el remitente informa al admitir el envío. Es lo que habilita la cobertura completa descrita en la política de indemnizaciones.

**Costo del seguro:** 1,2% del valor declarado, con un mínimo de $2.800 por envío. Por ejemplo, declarar $500.000 cuesta $6.000 de seguro.

**Límites:**

- Valor declarado máximo por envío: $8.000.000. Por encima de ese monto se requiere autorización de un ejecutivo comercial antes de admitirlo.
- Sin seguro, el tope vigente de indemnización por daño o pérdida es de $300.000, independientemente del peso. Este valor se revisa cada año y debe confirmarse contra el tarifario antes de comunicarlo a un cliente.

**Qué documentos pide una reclamación asegurada:** factura o soporte de compra del contenido, fotos del empaque y del daño, y la guía de transporte.

**Qué no cubre el seguro:** artículos de la lista de prohibidos (códigos P-01 a P-10), daños por empaque inadecuado según el manual de embalaje, y envíos cuyo contenido fue declarado de forma falsa.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "13-atencion-pqr.md": """# Atención de Peticiones, Quejas y Reclamos (PQR)

Cualquier persona puede presentar una petición, queja, reclamo o sugerencia sobre el servicio, aunque no sea cliente registrado.

**Canales:**

- Línea de servicio: (601) 555-0142, de lunes a viernes de 7:00 a.m. a 7:00 p.m. y sábados de 8:00 a.m. a 1:00 p.m.
- Correo electrónico: pqr@ejemplo-ir.test
- Chat de la página web, las 24 horas, atendido por el asistente virtual y escalado a un agente en horario hábil.

**Radicado:** toda PQR recibe un número de radicado con el formato PQR-AAAA-NNNNNN (por ejemplo, PQR-2026-004821). Sin radicado no hay seguimiento posible, así que el agente debe entregarlo siempre.

**Tiempos máximos de respuesta:**

- Petición de información: 10 días hábiles.
- Queja: 15 días hábiles.
- Reclamo sobre un envío: 15 días hábiles, que corren en paralelo con el SLA de investigación del caso.
- Sugerencia: 20 días hábiles.

Si el plazo se vence sin respuesta, el caso se escala automáticamente al coordinador de servicio.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "14-envios-internacionales.md": """# Envíos Internacionales a la Región Andina

Los envíos internacionales se ofrecen únicamente hacia Ecuador, Perú y Bolivia.

**Límites del servicio:**

- Peso máximo por paquete: 20 kg.
- Lado mayor máximo: 120 cm.
- Tiempo estimado de entrega: 6 a 10 días hábiles después de que el envío sale de Colombia; no incluye demoras por revisión aduanera.

**Documentos obligatorios:**

- Factura comercial con la descripción detallada del contenido y su valor.
- Formulario DEX (declaración de exportación) cuando el valor supera USD 1.000.
- Subpartida arancelaria de cada artículo; sin ella la aduana puede retener el paquete.

El remitente es responsable de la exactitud de los datos declarados. Los impuestos y aranceles del país de destino los paga el destinatario, salvo pacto distinto con el cliente corporativo.

Los artículos restringidos en el país de destino pueden diferir de la lista nacional; ante la duda se debe consultar al área de comercio exterior antes de admitir el envío.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "15-horarios-ventanas-entrega.md": """# Horarios y Ventanas de Entrega

Las entregas se programan en dos ventanas: **mañana** de 8:00 a.m. a 12:00 m. y **tarde** de 12:00 m. a 6:00 p.m. El destinatario puede pedir una ventana concreta, que se respeta si el envío todavía no salió a reparto.

**Sábados:** solo hay entrega en las ciudades principales, hasta la 1:00 p.m.

**Domingos y festivos:** no hay entrega, salvo el servicio Same Day corporativo con contrato vigente.

**Intentos:** el mensajero hace un intento por visita. Si el destinatario no está, deja un aviso con el teléfono de la oficina y el siguiente intento se programa para el siguiente día hábil.

**Reprogramación:** el destinatario puede reprogramar sin costo hasta 2 veces. A partir de la tercera reprogramación el intento cuenta como fallido y avanza hacia la devolución.

**Hora de corte para admisión:** los envíos admitidos antes de las 4:00 p.m. salen en la ruta del día siguiente hábil; los admitidos después salen un día más tarde.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "16-clientes-corporativos.md": """# Clientes Corporativos y Contrato Marco

Un cliente corporativo es una empresa que envía con regularidad y firma un contrato marco con tarifas negociadas.

**Facturación:** quincenal, con corte los días 15 y el último día de cada mes. El plazo de pago es de 30 días desde la fecha de factura.

**Descuento por volumen mensual de envíos:**

- 500 a 1.999 envíos al mes: 8% sobre el flete.
- 2.000 o más envíos al mes: 12% sobre el flete.

El descuento no aplica al seguro ni a los recargos por zona de difícil acceso.

**Beneficios adicionales:** ejecutivo de cuenta asignado, recolección programada sin costo, acceso a reportes de envíos por correo y a la integración por API con notificaciones automáticas de cambios de estado.

**Cambio de condiciones:** las tarifas del contrato se revisan una vez al año. Ningún agente de servicio puede prometer un descuento distinto al del contrato; cualquier excepción la aprueba el ejecutivo comercial.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "17-recoleccion-domicilio.md": """# Recolección a Domicilio

La recolección a domicilio es el servicio en el que un mensajero pasa por la dirección del remitente a recoger los paquetes.

**Cómo pedirla:** con al menos 4 horas de anticipación, por la línea de servicio o por la página web. Se agenda en una ventana de dos horas.

**Costo:**

- Recolección de 1 a 9 paquetes: $3.500 por visita.
- Desde 10 paquetes por visita: sin costo.
- Clientes corporativos con recolección programada: sin costo, según contrato.

**Servicio Same Day:** para que el envío salga el mismo día, la recolección debe estar confirmada antes de las 11:00 a.m.

**Cancelación:** se puede cancelar sin cargo hasta 1 hora antes de la ventana. Si el mensajero llega y no hay paquetes, se cobra la visita fallida de $3.500.

El remitente debe entregar los paquetes ya empacados y rotulados según el manual de embalaje; el mensajero no empaca.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
    "18-notificaciones-seguimiento.md": """# Notificaciones y Seguimiento de Envíos

El número de guía es un código numérico de 6 a 15 dígitos que se entrega al admitir el envío. Con él se consulta el estado en la página web, por el chat o por correo.

**Notificaciones automáticas:** el remitente y el destinatario reciben un mensaje por SMS o correo cuando el envío cambia a: admitido, en tránsito, en ruta de entrega, entregado y novedad.

**Prueba de entrega (POD):** incluye la firma y la fotografía del paquete entregado. Está disponible 24 horas después de la entrega y se conserva durante 12 meses.

**Quién puede ver los datos:** solo se comparte el estado de un envío con el remitente, el destinatario o la persona que escribe desde el correo registrado para ese envío. Si alguien pregunta por una guía que no le pertenece, la respuesta es la misma que si la guía no existiera.

**Frecuencia de actualización:** el estado se actualiza cuando el paquete pasa por un punto de control (escaneo). Un envío puede pasar varias horas sin cambios entre dos puntos de control sin que eso sea un retraso.

*Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG.*
""",
}

# ---------------------------------------------------------------------------
# PDF: nombre de archivo (sin extension) -> (titulo, [(encabezado, texto)]).
# Solo caracteres Latin-1 (las fuentes base de PDF no traen Unicode); el
# generador normaliza los pocos simbolos tipograficos y falla si queda algo.
# ---------------------------------------------------------------------------
PDF_DOCS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "Condiciones del contrato de transporte": (
        "Condiciones Generales del Contrato de Transporte de Envíos",
        [
            ("Artículo 1. Objeto",
             "El presente contrato regula el transporte de paquetes y documentos entre las "
             "ciudades cubiertas por la red de la empresa, bajo las modalidades Estándar, "
             "Express y Same Day."),
            ("Artículo 2. Obligaciones del remitente",
             "El remitente debe declarar de forma veraz el contenido, empacarlo según el "
             "manual de embalaje y no enviar artículos de la lista de prohibidos. La "
             "declaración falsa libera a la empresa de toda responsabilidad por pérdida o daño."),
            ("Artículo 3. Peso y dimensiones",
             "Cada paquete pesa como máximo 30 kg y su lado mayor no supera 150 cm. La suma "
             "de largo, ancho y alto no excede 300 cm. Los paquetes que superen estos límites "
             "se tratan como carga especial con tarifa distinta."),
            ("Artículo 4. Tarifa y pago",
             "La tarifa se calcula por el mayor entre el peso real y el peso volumétrico "
             "(largo x ancho x alto en cm, dividido por 5.000). El pago se hace al admitir el "
             "envío, salvo clientes corporativos con crédito."),
            ("Artículo 5. Plazos de entrega",
             "Los tiempos publicados son estimados y se cuentan en días hábiles desde la "
             "admisión. No incluyen demoras por novedades atribuibles al remitente o al "
             "destinatario, ni por fuerza mayor."),
            ("Artículo 6. Intentos de entrega",
             "La empresa realiza hasta tres intentos de entrega. Agotados, el envío queda en "
             "custodia durante 15 días calendario y luego se devuelve al remitente a su costo."),
            ("Artículo 7. Reclamaciones",
             "Toda reclamación debe presentarse dentro de los 30 días calendario siguientes a "
             "la admisión del envío. Los daños aparentes deben informarse dentro de las 24 "
             "horas siguientes a la entrega."),
            ("Artículo 8. Valor declarado",
             "El valor declarado máximo por envío es de $8.000.000. El seguro cuesta el 1,2% "
             "del valor declarado, con un mínimo de $2.800."),
            ("Artículo 9. Limitación de responsabilidad",
             "Sin valor declarado y seguro, la indemnización por daño o pérdida se limita al "
             "tope vigente de $300.000. En ningún caso se indemnizan lucro cesante ni daños "
             "indirectos."),
            ("Artículo 10. Envíos prohibidos y restringidos",
             "No se transportan los artículos prohibidos (códigos P-01 a P-10). Los "
             "restringidos (códigos R-01 a R-08) requieren embalaje y autorización especiales."),
            ("Artículo 11. Datos personales",
             "Los datos de remitente y destinatario se tratan conforme a la Ley 1581 de 2012 y "
             "a la política de tratamiento de datos personales de la empresa."),
            ("Artículo 12. Ley aplicable",
             "El contrato se rige por la ley colombiana. Las diferencias se resuelven primero "
             "por la vía de PQR y, agotada esta, ante la autoridad competente."),
        ],
    ),
    "Manual de embalaje y empaque": (
        "Manual de Embalaje y Empaque",
        [
            ("1. Principio general",
             "Un buen empaque protege el contenido de golpes, humedad y compresión durante "
             "todo el trayecto. Un daño atribuible al empaque inadecuado no está cubierto por "
             "el seguro."),
            ("2. Cajas",
             "Use cajas de cartón corrugado de pared sencilla para hasta 10 kg y de pared "
             "doble para más de 10 kg y hasta 30 kg. No reutilice cajas con esquinas "
             "aplastadas o con humedad."),
            ("3. Relleno",
             "Deje al menos 5 cm de material de relleno (papel kraft arrugado, espuma o "
             "burbuja) entre el contenido y cada pared de la caja. El contenido no debe "
             "moverse al agitar la caja."),
            ("4. Cierre",
             "Selle todas las uniones con cinta de empaque de mínimo 48 mm de ancho, en "
             "forma de H. No use cinta de enmascarar ni cinta adhesiva de oficina."),
            ("5. Artículos frágiles",
             "Envuelva cada pieza por separado y marque la caja con el rótulo FRÁGIL en dos "
             "caras opuestas. Un artículo declarado frágil requiere seguro."),
            ("6. Líquidos",
             "Los envases de líquidos van dentro de una bolsa sellada y con material "
             "absorbente. Los líquidos inflamables son artículos prohibidos."),
            ("7. Rotulado",
             "Pegue el rótulo con la guía en la cara mayor de la caja, sin cubrirlo con "
             "cinta. Incluya dentro del paquete una copia de los datos del destinatario."),
            ("8. Peso volumétrico",
             "Una caja grande y liviana se cobra por peso volumétrico: largo x ancho x alto "
             "en cm dividido por 5.000. Ajustar la caja al contenido reduce el costo."),
        ],
    ),
    "Circular 2026-014 Recaudo contra entrega": (
        "Circular 2026-014 - Cambios en el servicio de recaudo contra entrega",
        [
            ("Asunto",
             "Modificación del plazo de liquidación del recaudo y de los límites por envío, en "
             "aplicación de la Resolución interna RI-2026-031."),
            ("1. Plazo de liquidación",
             "A partir del 1 de marzo de 2026 el recaudo se liquida al remitente a los 3 días "
             "hábiles después de la entrega. Antes de esa fecha el plazo era de 5 días hábiles."),
            ("2. Valor máximo por envío",
             "El valor máximo a recaudar por envío pasa de $1.500.000 a $2.000.000. Valores "
             "superiores requieren autorización del área comercial."),
            ("3. Comisión",
             "La comisión por recaudo es del 2,5% del valor cobrado, con un mínimo de $1.800 "
             "por envío. La comisión se descuenta en la liquidación."),
            ("4. Medios de pago aceptados",
             "El mensajero solo recibe efectivo. El pago con tarjeta o transferencia en la "
             "puerta no está habilitado todavía."),
            ("5. Envíos no entregados",
             "Si el envío no se entrega y se devuelve, no se genera recaudo ni comisión. El "
             "remitente solo paga el flete de la devolución."),
            ("6. Vigencia",
             "La presente circular rige desde su publicación y deroga la Circular 2025-009."),
        ],
    ),
    "Política de tratamiento de datos personales": (
        "Política de Tratamiento de Datos Personales",
        [
            ("1. Marco legal",
             "Esta política se expide en cumplimiento de la Ley 1581 de 2012 y sus decretos "
             "reglamentarios sobre protección de datos personales."),
            ("2. Datos que se recolectan",
             "Nombre, documento de identidad, dirección, teléfono y correo del remitente y "
             "del destinatario, y la firma y fotografía de la prueba de entrega."),
            ("3. Finalidad",
             "Los datos se usan para ejecutar el transporte, notificar el estado del envío, "
             "atender PQR y cumplir obligaciones legales. No se venden a terceros."),
            ("4. Conservación",
             "La prueba de entrega se conserva por 12 meses. Los datos de facturación se "
             "conservan por el tiempo que exija la ley tributaria."),
            ("5. Derechos del titular",
             "El titular puede conocer, actualizar, rectificar y suprimir sus datos, y revocar "
             "la autorización. La solicitud se resuelve en un máximo de 15 días hábiles."),
            ("6. Canal para ejercer los derechos",
             "Las solicitudes se radican por el correo datos@ejemplo-ir.test o por la linea "
             "(601) 555-0142 y reciben un radicado con formato PQR-AAAA-NNNNNN."),
            ("7. Atención por correo electrónico",
             "Al responder consultas por correo, solo se comparte información de un envío con "
             "quien escribe desde el correo registrado del cliente de ese envío."),
        ],
    ),
}

# ---------------------------------------------------------------------------
# DOCX: nombre de archivo (sin extension) -> lista de bloques.
#   ("h1"|"h2", texto) | ("p", texto) | ("li", texto)
#   ("table", [encabezados], [[fila], ...])
# ---------------------------------------------------------------------------
DOCX_DOCS: dict[str, list[tuple]] = {
    "Procedimiento interno de atencion de reclamos": [
        ("h1", "Procedimiento interno de atención de reclamos"),
        ("p", "Este procedimiento lo siguen los analistas de servicio al recibir una reclamación sobre un envío."),
        ("h2", "1. Recepción y clasificación"),
        ("li", "Verificar que quien reclama es el remitente, el destinatario o un autorizado."),
        ("li", "Crear el caso y entregar el radicado con formato PQR-AAAA-NNNNNN."),
        ("li", "Clasificar el caso con uno de los códigos REC de la tabla de tiempos."),
        ("h2", "2. Tiempos por tipo de caso"),
        ("table",
         ["Código", "Tipo de caso", "SLA de investigación (horas)", "Responsable"],
         [
             ["REC-01", "Daño del contenido", "72", "Analista de logística"],
             ["REC-02", "Pérdida del envío", "120", "Analista de logística"],
             ["REC-03", "Retraso extremo", "96", "Coordinador de ruta"],
             ["REC-04", "Cobro incorrecto", "48", "Facturación"],
             ["REC-05", "Entrega a persona no autorizada", "120", "Supervisor de servicio"],
         ]),
        ("h2", "3. Investigación"),
        ("p", "El analista revisa el historial de escaneos, contacta la oficina de destino y, en casos de daño, solicita fotos del empaque. Mientras el caso está en investigación activa no se promete ningúna indemnización."),
        ("h2", "4. Escalamiento"),
        ("li", "Si el SLA se vence sin diagnóstico, el caso pasa al supervisor de servicio."),
        ("li", "Si pasan 10 días hábiles totales sin ningún evento nuevo, el envío se declara perdido (REC-02)."),
        ("li", "Las reclamaciones por más de $2.000.000 requieren aprobación del coordinador regional."),
        ("h2", "5. Cierre"),
        ("p", "El caso se cierra con el diagnóstico confirmado y la acción tomada: indemnización, devolución del flete o corrección de facturación. El cliente recibe la respuesta por el mismo canal en que reclamó."),
        ("p", "Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG."),
    ],
    "Articulos prohibidos y restringidos": [
        ("h1", "Artículos prohibidos y restringidos"),
        ("p", "Esta lista la consulta el personal de admisión antes de recibir un paquete. Ante la duda, no se admite hasta consultar al supervisor."),
        ("h2", "Artículos prohibidos"),
        ("table",
         ["Código", "Artículo", "Motivo"],
         [
             ["P-01", "Armas de fuego y municiones", "Normativa de control de armas"],
             ["P-02", "Explosivos y pirotecnia", "Riesgo para el personal y la carga"],
             ["P-03", "Sustancias psicoactivas ilegales", "Delito"],
             ["P-04", "Dinero en efectivo y títulos valores al portador", "Riesgo de lavado y pérdida"],
             ["P-05", "Líquidos inflamables", "Riesgo de incendio"],
             ["P-06", "Material biológico infeccioso", "Riesgo sanitario"],
             ["P-07", "Animales vivos", "Bienestar animal y trazabilidad"],
             ["P-08", "Restos humanos", "Normativa sanitaria"],
             ["P-09", "Mercancía falsificada", "Propiedad intelectual"],
             ["P-10", "Material radiactivo", "Riesgo para la salud"],
         ]),
        ("h2", "Artículos restringidos"),
        ("p", "Se admiten solo con embalaje especial y, cuando se indica, con autorización previa."),
        ("table",
         ["Código", "Artículo", "Condición de admisión"],
         [
             ["R-01", "Baterías de litio", "Máximo 2 por paquete, protegidas contra cortocircuito"],
             ["R-02", "Aerosoles", "Solo en envíos terrestres, máximo 1 litro por paquete"],
             ["R-03", "Perfumes y alcoholes", "Máximo 500 ml por envase, sellados"],
             ["R-04", "Medicamentos", "Con fórmula médica y empaque original"],
             ["R-05", "Alimentos perecederos", "Solo Same Day o Express urbano"],
             ["R-06", "Joyas y relojes de alto valor", "Seguro obligatorio y valor declarado"],
             ["R-07", "Equipos electrónicos frágiles", "Seguro obligatorio y rótulo FRAGIL"],
             ["R-08", "Plantas y semillas", "Con certificado fitosanitario"],
         ]),
        ("p", "Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG."),
    ],
    "Instructivo de recaudo contra entrega": [
        ("h1", "Instructivo de recaudo contra entrega"),
        ("p", "Guía para el mensajero y para el analista que atiende dudas sobre el servicio de recaudo (pago contra entrega)."),
        ("h2", "Pasos del mensajero"),
        ("li", "Confirmar el valor a cobrar que aparece en el rótulo de la guía."),
        ("li", "Cobrar únicamente en efectivo y entregar el recibo."),
        ("li", "Registrar el cobro en el dispositivo antes de salir del domicilio."),
        ("li", "Consignar el dinero en la oficina al final de la ruta."),
        ("h2", "Límites y condiciones"),
        ("table",
         ["Concepto", "Valor", "Observación"],
         [
             ["Valor máximo a recaudar por envío", "$2.000.000", "Superior requiere autorización comercial"],
             ["Comisión por recaudo", "2,5%", "Mínimo $1.800 por envío"],
             ["Plazo de liquidación al remitente", "3 días hábiles", "Contados desde la entrega"],
             ["Medio de pago aceptado", "Efectivo", "No se recibe tarjeta ni transferencia"],
         ]),
        ("h2", "Situaciones frecuentes"),
        ("li", "El destinatario no tiene el dinero completo: no se entrega el paquete y se registra la novedad N-11."),
        ("li", "El destinatario rechaza el paquete: no se genera recaudo y el envío entra al proceso de devolución."),
        ("li", "El billete es sospechoso: no se recibe y se avisa al supervisor de la oficina."),
        ("p", "Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG."),
    ],
    "Guia de tono y respuestas para atencion al cliente": [
        ("h1", "Guía de tono y respuestas para atención al cliente"),
        ("p", "Esta guía define cómo debe sonar una respuesta de servicio, ya la escriba una persona o el asistente virtual."),
        ("h2", "Principios"),
        ("li", "Empezar con el dato que el cliente pidió, no con una disculpa genérica."),
        ("li", "Usar el tratamiento de usted, frases cortas y sin tecnicismos internos."),
        ("li", "No prometer fechas ni indemnizaciones que dependan de una investigación abierta."),
        ("li", "Si no se tiene la información, decirlo y explicar qué sigue y quién puede ayudar."),
        ("h2", "Cuándo pedir disculpas"),
        ("p", "Se pide disculpas cuando hay una falla confirmada de la empresa: retraso por causa interna, error de digitación o daño en bodega. No se pide disculpas por una política vigente, sino que se explica."),
        ("h2", "Frases a evitar"),
        ("table",
         ["Evitar", "Usar en su lugar"],
         [
             ["Eso no es problema nuestro", "Voy a revisar qué pasó con su envío"],
             ["Es política de la empresa", "Esto funciona así porque el plazo existe para proteger el envío"],
             ["Le garantizo que llega mañana", "El tiempo estimado es de 2 a 4 días hábiles"],
             ["No sé", "No tengo ese dato; puedo escalarlo al área responsable"],
         ]),
        ("h2", "Cierre de la conversación"),
        ("p", "Confirmar que se resolvió la duda, dar el número de radicado si se creó un caso y ofrecer ayuda adicional una sola vez."),
        ("p", "Documento sintético de prueba: contenido inventado para evaluar la búsqueda del RAG."),
    ],
}
