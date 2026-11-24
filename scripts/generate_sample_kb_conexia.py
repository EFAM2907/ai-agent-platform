"""Genera el corpus SINTETICO de Conexia Contact Center (tenant de prueba
tipo BPO / call center, en el estilo de Konecta o Teleperformance) en
kb_content/conexia_contact_center/: documentos md, pdf, docx y xlsx con
contenido INVENTADO. Nada de esto es informacion real de ninguna empresa;
cada documento lo dice en su pie.

Idempotente: reescribe siempre los mismos archivos.

    pip install -r requirements-dev.txt
    python -m scripts.generate_sample_kb_conexia

Despues, para llevarlo a la base:

    python -m scripts.ingest_kb_articles <organization_id> --dir conexia_contact_center
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from fpdf import FPDF
from openpyxl import Workbook

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "kb_content" / "conexia_contact_center"

# Los PDF usan fuentes core (latin-1): nada de comillas curvas ni guiones largos.
NOTICE = (
    "Documento sintetico de prueba: contenido inventado, no es informacion "
    "real de ninguna empresa."
)

MARKDOWN_DOCS: dict[str, str] = {
    "01-protocolo-atencion-telefonica.md": """# Protocolo de Atención Telefónica

Todo agente de Conexia sigue la misma estructura de llamada, sin importar la campaña.

**Estructura de la llamada (modelo SALVE):**

1. **Saludo**: contestar antes del tercer timbre con el saludo de la campaña y el nombre del agente.
2. **Autenticación**: validar la identidad del cliente con dos datos (documento y fecha de nacimiento) antes de dar información de cuenta.
3. **Diagnóstico**: escuchar sin interrumpir y resumir el motivo de la llamada en una frase.
4. **Solución**: ofrecer la solución en el primer contacto siempre que esté dentro de la autonomía del agente.
5. **Cierre**: confirmar que el cliente quedó satisfecho, informar el número de radicado y despedirse con el cierre de la campaña.

**Reglas de oro:**

- Tiempo máximo de silencio en línea: 15 segundos. Si se necesita más, pedir permiso y poner en espera con música.
- Tiempo máximo en espera: 90 segundos por tramo. Pasado ese tiempo se retoma la llamada y se informa el avance.
- Nunca se discute con el cliente ni se cuelga primero, salvo llamadas con insultos reiterados después de dos advertencias (código de tipificación `ABU-01`).
- Toda llamada termina con un radicado en el CRM, aunque el cliente no lo pida.
""",
    "02-niveles-de-servicio-sla.md": """# Niveles de Servicio (SLA) por Canal

Los acuerdos de nivel de servicio base de Conexia, que cada campaña puede endurecer por contrato:

| Indicador | Meta estándar |
| --- | --- |
| Nivel de servicio voz (ASA) | 80% de las llamadas contestadas en 20 segundos |
| Tasa de abandono | Menos del 5% |
| Tiempo medio de operación (AHT) voz | 6 minutos 30 segundos |
| Primera respuesta en chat | 30 segundos |
| Primera respuesta en WhatsApp | 2 minutos en horario de atención |
| Primera respuesta en correo | 4 horas hábiles |
| Resolución en primer contacto (FCR) | 78% |
| Satisfacción del cliente (CSAT) | 4,3 sobre 5 |

**Penalidades:** si el nivel de servicio mensual de una campaña cae por debajo del 70%, Conexia reconoce al cliente un descuento del 3% de la facturación del mes. Por debajo del 60% el descuento es del 6% y se convoca un comité de mejora en los 5 días hábiles siguientes.

Los indicadores se miden por intervalo de 30 minutos y se publican en el tablero de operaciones cada hora.
""",
    "03-escalamiento-de-incidencias.md": """# Escalamiento de Incidencias

Cuando un agente no puede resolver un caso dentro de su autonomía, lo escala siguiendo estos niveles:

- **Nivel 1 (agente)**: resuelve consultas frecuentes con la base de conocimiento. Autonomía de hasta $50.000 en ajustes o compensaciones.
- **Nivel 2 (supervisor de piso)**: atiende llamadas escaladas por el cliente y aprueba compensaciones hasta $300.000. Debe contestar la solicitud del agente en máximo 5 minutos.
- **Nivel 3 (coordinador de campaña)**: casos con riesgo reputacional, quejas ante la Superintendencia o compensaciones superiores a $300.000. Respuesta en máximo 4 horas hábiles.
- **Nivel 4 (gerente de cuenta)**: crisis, caídas de plataforma del cliente o incidentes de seguridad de la información.

**Cuándo escalar de inmediato (sin agotar niveles):**

- El cliente menciona una queja formal, tutela o acción legal.
- Sospecha de fraude o suplantación de identidad.
- Amenazas de daño personal: se activa el protocolo de seguridad y se avisa al supervisor en línea.

Todo escalamiento queda registrado en el CRM con la tipificación `ESC-02` y el nombre de quien lo recibe.
""",
    "04-turnos-y-ausentismo.md": """# Turnos, Descansos y Ausentismo

**Jornada:** 7 horas y 36 minutos efectivas de lunes a sábado en turnos rotativos, con un domingo libre cada 15 días.

**Pausas:**

- Dos pausas activas de 10 minutos, una por cada mitad del turno.
- Almuerzo de 45 minutos, programado por el planificador de personal (WFM).
- Pausas para baño: máximo 2 de 5 minutos por turno, sin necesidad de aprobación.

**Cambios de turno:** se solicitan con mínimo 48 horas de antelación a través del portal del empleado, y deben ser aceptados por un compañero del mismo perfil y aprobados por el supervisor.

**Ausentismo:**

- Aviso de ausencia: mínimo 2 horas antes del inicio del turno, por la línea de novedades de operaciones.
- Incapacidades médicas: se entregan al área de Gestión Humana dentro de las 48 horas siguientes.
- Tres llegadas tarde de más de 10 minutos en un mes generan un llamado de atención verbal; seis, uno escrito.
- La meta de ausentismo de la operación es inferior al 6% mensual.
""",
    "05-calidad-y-monitoreo.md": """# Calidad y Monitoreo de Interacciones

El equipo de Calidad evalúa a cada agente con una muestra mensual:

- **8 interacciones por agente al mes** (4 de voz y 4 de canales digitales), elegidas al azar por el sistema de grabación.
- La evaluación usa la matriz de calidad con 20 criterios agrupados en tres bloques: **Atención** (40%), **Solución** (40%) y **Cumplimiento** (20%).
- Meta mínima de calidad: **90%**. Entre 80% y 89% el agente entra en plan de refuerzo; por debajo de 80% se programa coaching individual en la semana siguiente.

**Errores críticos** (anulan la evaluación completa y valen 0%):

- No autenticar al cliente antes de dar información de la cuenta.
- Dar información falsa o inventada.
- Maltrato al cliente.
- No cumplir el guion legal de grabación (aviso de llamada grabada).

**Calibraciones:** cada mes se realiza una sesión con los analistas de Calidad y el cliente para alinear criterios. El agente puede disputar una evaluación dentro de los 3 días hábiles siguientes a recibirla.
""",
    "06-canales-omnicanal.md": """# Atención Omnicanal

Conexia atiende por los siguientes canales, con reglas propias de simultaneidad:

| Canal | Atención simultánea por agente | Horario estándar |
| --- | --- | --- |
| Voz | 1 llamada | 24/7 según campaña |
| Chat web | Hasta 3 conversaciones | 7:00 a 22:00 |
| WhatsApp Business | Hasta 4 conversaciones | 7:00 a 20:00 |
| Correo electrónico | Cola asíncrona | Lunes a sábado, 8:00 a 18:00 |
| Redes sociales | Hasta 2 conversaciones | 8:00 a 20:00 |

**Reglas:**

- Una conversación de WhatsApp se cierra automáticamente tras 24 horas sin respuesta del cliente.
- Si un cliente cambia de canal, el agente debe retomar el radicado existente en lugar de abrir uno nuevo.
- En redes sociales, los datos personales nunca se piden en público: se invita al cliente a un mensaje directo.
- Las plantillas de WhatsApp deben estar aprobadas por el cliente antes de usarse en campañas de salida.
""",
}

PDF_DOCS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "Politica de proteccion de datos y grabacion de llamadas": (
        "Politica de Proteccion de Datos y Grabacion de Llamadas",
        [
            (
                "1. Alcance",
                "Aplica a todos los empleados, contratistas y proveedores de Conexia Contact Center "
                "que traten datos personales de los clientes finales de las campañas, de acuerdo con "
                "la Ley 1581 de 2012 y las instrucciones contractuales de cada cliente.",
            ),
            (
                "2. Grabacion de llamadas",
                "Todas las llamadas se graban con fines de calidad, formacion y soporte legal. Al "
                "inicio de cada llamada el agente debe informar: 'Esta llamada sera grabada y "
                "monitoreada para fines de calidad'. Las grabaciones se conservan 18 meses en el "
                "repositorio seguro y luego se eliminan, salvo que exista una orden de retencion "
                "legal o una reclamacion en curso.",
            ),
            (
                "3. Datos sensibles",
                "Nunca se solicita ni se registra el numero completo de la tarjeta de credito, el "
                "codigo de seguridad (CVV) ni claves del cliente. Cuando el cliente va a dictar "
                "datos de pago, el agente activa la pausa de grabacion (tecla F9) y la reactiva al "
                "terminar. Dictar datos de pago con la grabacion activa es un error critico de calidad.",
            ),
            (
                "4. Puestos de trabajo seguros",
                "Politica de escritorio limpio: no se permiten celulares, papel ni lapiceros en la "
                "sala de operacion. Las pantallas se bloquean automaticamente a los 2 minutos de "
                "inactividad. Esta prohibido copiar, fotografiar o exportar datos de clientes.",
            ),
            (
                "5. Incidentes",
                "Cualquier sospecha de fuga de informacion debe reportarse al oficial de proteccion "
                "de datos (correo seguridad@conexia.example.com) dentro de las 2 horas siguientes. "
                "El cliente afectado es notificado por la gerencia de cuenta en un maximo de 72 horas.",
            ),
        ],
    ),
    "Manual de induccion para nuevos agentes": (
        "Manual de Induccion para Nuevos Agentes",
        [
            (
                "1. Estructura de la formacion",
                "La formacion inicial dura 3 semanas: semana 1 de cultura y herramientas (CRM, "
                "softphone, base de conocimiento); semana 2 de producto de la campaña y simulacros; "
                "semana 3 de nesting, atendiendo llamadas reales con acompañamiento de un mentor.",
            ),
            (
                "2. Criterios de aprobacion",
                "Para pasar a produccion el agente debe obtener minimo 85% en la evaluacion de "
                "producto, 90% en el simulacro de calidad y asistencia del 95% a las sesiones. Quien "
                "no apruebe tiene una unica oportunidad de repetir la evaluacion en 3 dias habiles.",
            ),
            (
                "3. Periodo de nesting",
                "Durante el nesting el agente atiende hasta 60% de la carga normal y su AHT objetivo "
                "es 30% mayor al de un agente experimentado. El mentor revisa 5 interacciones por "
                "dia y entrega retroalimentacion al finalizar cada turno.",
            ),
            (
                "4. Herramientas",
                "Cada agente recibe usuario de red, usuario de CRM y diadema. El perfil de acceso "
                "depende de la campaña: nadie tiene acceso a campañas distintas a la suya. Las "
                "claves se cambian cada 60 dias.",
            ),
            (
                "5. Beneficios",
                "Auxilio de transporte, bono de alimentacion, bono por cumplimiento de metas "
                "(ver escala de bonificaciones) y plan de carrera hacia analista, supervisor y "
                "coordinador. Los agentes con 12 meses y calidad superior a 92% pueden postularse "
                "a movilidad interna.",
            ),
        ],
    ),
}

DOCX_DOCS: dict[str, list[tuple]] = {
    "Guia de manejo de objeciones y clientes molestos": [
        ("h1", "Guía de Manejo de Objeciones y Clientes Molestos"),
        ("p", "Esta guía resume la técnica EMPATÍA-SOLUCIÓN que usan todos los agentes de Conexia."),
        ("h2", "Técnica en cuatro pasos"),
        ("li", "Escuchar: dejar que el cliente termine, sin interrumpir, hasta por 60 segundos."),
        ("li", "Validar: reconocer la emoción con una frase corta («entiendo que esto le haya causado molestia»)."),
        ("li", "Proponer: ofrecer una solución concreta y una fecha de cumplimiento."),
        ("li", "Confirmar: preguntar si la solución es aceptable y registrar el compromiso."),
        ("h2", "Frases recomendadas y frases prohibidas"),
        (
            "table",
            ["Situación", "Decir", "Evitar"],
            [
                ["Cliente molesto por demora", "Entiendo su frustración, voy a revisar esto ahora mismo.", "Eso no depende de mí."],
                ["No hay solución inmediata", "Lo que sí puedo hacer es radicar su caso con prioridad y llamarle en 24 horas.", "No se puede."],
                ["Cliente pide un supervisor", "Con gusto, le paso con mi supervisor y le dejo el caso resumido.", "Mi supervisor dirá lo mismo."],
                ["Cliente insulta", "Quiero ayudarle, pero necesito que nos hablemos con respeto.", "Contestar el insulto."],
            ],
        ),
        ("h2", "Regla de las tres advertencias"),
        ("p", "Si el cliente mantiene el maltrato, el agente advierte dos veces con cortesía. A la tercera, informa que finalizará la llamada y tipifica el caso como ABU-01."),
    ],
    "Plan de continuidad del negocio": [
        ("h1", "Plan de Continuidad del Negocio (BCP)"),
        ("p", "Define cómo mantiene Conexia la operación ante caídas de energía, de conectividad o de una sede."),
        ("h2", "Escenarios y respuesta"),
        (
            "table",
            ["Escenario", "Tiempo máximo de recuperación", "Acción"],
            [
                ["Caída de energía en la sede", "5 minutos", "Entra la UPS y luego la planta eléctrica; los equipos críticos no se apagan."],
                ["Caída del canal de internet principal", "2 minutos", "Conmutación automática al canal secundario de otro proveedor."],
                ["Sede no disponible (incendio, inundación)", "4 horas", "Se activa el teletrabajo de contingencia con el 60% de los agentes."],
                ["Caída del CRM del cliente", "30 minutos", "Se atiende en modo manual con formulario de contingencia y se carga después."],
            ],
        ),
        ("h2", "Teletrabajo de contingencia"),
        ("li", "Solo agentes con equipo de la empresa y VPN activa."),
        ("li", "Se mantiene la grabación y las mismas reglas de protección de datos."),
        ("li", "El coordinador de campaña confirma la capacidad disponible a la gerencia en los primeros 30 minutos."),
        ("h2", "Simulacros"),
        ("p", "Se realiza un simulacro completo cada semestre y se documenta un informe de lecciones aprendidas para cada cliente."),
    ],
}

WORKBOOKS: dict[str, dict[str, tuple[list[str], list[list]]]] = {
    "Catalogo de campanas": {
        "Campanas": (
            ["Campaña", "Sector", "Canal principal", "Horario", "Agentes", "Meta de nivel de servicio", "Idioma"],
            [
                ["Banco Andino - Servicio al cliente", "Financiero", "Voz", "24/7", 120, "80/20", "Español"],
                ["Banco Andino - Cobranza temprana", "Financiero", "Voz", "Lunes a sábado 8:00-19:00", 65, "70/30", "Español"],
                ["Telco Nova Hogar - Soporte técnico", "Telecomunicaciones", "Voz y chat", "7:00-23:00", 90, "80/20", "Español"],
                ["EPS Salud Vida - Citas médicas", "Salud", "Voz y WhatsApp", "Lunes a viernes 6:00-20:00", 55, "85/20", "Español"],
                ["Retail Plaza - Pedidos en línea", "Comercio", "Chat y correo", "8:00-22:00", 40, "80/30", "Español"],
                ["Aerolínea Cóndor - Reservas", "Transporte", "Voz y correo", "24/7", 35, "80/20", "Español e inglés"],
            ],
        ),
    },
    "Tabla de tipificaciones": {
        "Tipificaciones": (
            ["Código", "Descripción", "Categoría", "Requiere escalamiento", "Plazo de gestión"],
            [
                ["CON-01", "Consulta de saldo o estado", "Consulta", "No", "Primer contacto"],
                ["REC-01", "Reclamo por cobro indebido", "Reclamo", "Sí, nivel 2", "5 días hábiles"],
                ["REC-02", "Reclamo por mala atención", "Reclamo", "Sí, nivel 2", "3 días hábiles"],
                ["REC-03", "Reclamo por incumplimiento de servicio", "Reclamo", "Sí, nivel 3", "8 días hábiles"],
                ["SOL-01", "Solicitud de cambio de datos", "Solicitud", "No", "24 horas"],
                ["SOL-02", "Solicitud de cancelación del servicio", "Solicitud", "Sí, retención", "Primer contacto"],
                ["ESC-02", "Escalamiento a supervisor", "Gestión interna", "Sí", "5 minutos"],
                ["FRA-01", "Sospecha de fraude", "Seguridad", "Sí, nivel 4", "Inmediato"],
                ["ABU-01", "Llamada finalizada por maltrato", "Gestión interna", "No", "Mismo día"],
                ["TEC-01", "Falla técnica con visita requerida", "Soporte", "Sí, nivel 2", "48 horas"],
            ],
        ),
    },
    "Escala de bonificaciones": {
        "Bonos": (
            ["Indicador", "Rango", "Bono mensual (COP)"],
            [
                ["Calidad", "90% a 94%", 120000],
                ["Calidad", "95% o más", 220000],
                ["Cumplimiento de AHT", "Dentro de ±10% de la meta", 90000],
                ["Asistencia perfecta", "Sin ausencias ni tardanzas", 150000],
                ["CSAT", "4,5 o más", 110000],
                ["Venta cruzada (campañas de ventas)", "Más de 15 ventas efectivas al mes", 180000],
            ],
        ),
        "Reglas": (
            ["Regla", "Detalle"],
            [
                ["Tope mensual", "El total de bonos no supera los 480.000 COP por agente"],
                ["Elegibilidad", "Se requiere calidad mínima de 90% y ningún error crítico en el mes"],
                ["Pago", "Junto con la nómina del mes siguiente"],
            ],
        ),
    },
}


def write_markdown(out: Path) -> None:
    for filename, content in MARKDOWN_DOCS.items():
        (out / filename).write_text(content + f"\n---\n_{NOTICE}_\n", encoding="utf-8", newline="\n")


def write_pdfs(out: Path) -> None:
    for filename, (title, sections) in PDF_DOCS.items():
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 15)
        pdf.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        for heading, body in sections:
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(0, 6, heading, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, body, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.multi_cell(0, 4, NOTICE, new_x="LMARGIN", new_y="NEXT")
        pdf.output(str(out / f"{filename}.pdf"))


def write_docx(out: Path) -> None:
    for filename, blocks in DOCX_DOCS.items():
        document = Document()
        for block in blocks:
            kind = block[0]
            if kind == "h1":
                document.add_heading(block[1], level=1)
            elif kind == "h2":
                document.add_heading(block[1], level=2)
            elif kind == "p":
                document.add_paragraph(block[1])
            elif kind == "li":
                document.add_paragraph(block[1], style="List Bullet")
            elif kind == "table":
                _, headers, rows = block
                table = document.add_table(rows=1, cols=len(headers))
                table.style = "Table Grid"
                for cell, header in zip(table.rows[0].cells, headers):
                    cell.text = header
                for row in rows:
                    cells = table.add_row().cells
                    for cell, value in zip(cells, row):
                        cell.text = str(value)
            else:
                raise ValueError(f"Bloque docx desconocido: {kind!r}")
        document.add_paragraph(NOTICE)
        document.save(str(out / f"{filename}.docx"))


def write_workbooks(out: Path) -> None:
    for filename, sheets in WORKBOOKS.items():
        workbook = Workbook()
        workbook.remove(workbook.active)
        for sheet_name, (headers, rows) in sheets.items():
            sheet = workbook.create_sheet(sheet_name)
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
        workbook.save(out / f"{filename}.xlsx")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_markdown(OUTPUT_DIR)
    write_pdfs(OUTPUT_DIR)
    write_docx(OUTPUT_DIR)
    write_workbooks(OUTPUT_DIR)
    print(f"Corpus sintetico escrito en {OUTPUT_DIR}")
    for path in sorted(OUTPUT_DIR.iterdir()):
        print(" ", path.name)


if __name__ == "__main__":
    main()
