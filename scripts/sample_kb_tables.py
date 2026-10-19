"""Tablas del corpus SINTETICO de prueba (xlsx) que genera
scripts/generate_sample_kb.py. Todo es inventado -- ver el docstring de
scripts/sample_kb_text.py. Los precios salen de una formula
determinista (base por ruta x multiplicador por peso x servicio), asi
que regenerar el corpus produce siempre los mismos numeros y las
preguntas de rag_golden.yaml no se desincronizan por accidente."""

from __future__ import annotations

# nombre de archivo (sin extension) -> {hoja: (encabezados, filas)}
Workbook = dict[str, tuple[list[str], list[list]]]

# ---------------------------------------------------------------------------
# Directorio de oficinas
# ---------------------------------------------------------------------------
_OFFICES = [
    # codigo, ciudad, nombre, direccion, L-V, sabado, telefono, servicios
    ("OFC-BOG-001", "Bogotá", "Centro Internacional", "Carrera 10 # 26-51", "7:00 a 19:00", "8:00 a 14:00", "(601) 555-0101", "Admisión, entrega, recaudo, recolección"),
    ("OFC-BOG-002", "Bogotá", "Chapinero", "Calle 63 # 11-20", "7:30 a 18:30", "8:00 a 13:00", "(601) 555-0102", "Admisión, entrega, recaudo"),
    ("OFC-BOG-003", "Bogotá", "Salitre", "Avenida Calle 26 # 69-76", "7:00 a 19:00", "8:00 a 14:00", "(601) 555-0103", "Admisión, entrega, carga especial"),
    ("OFC-BOG-004", "Bogotá", "Suba", "Carrera 91 # 146-30", "8:00 a 18:00", "8:00 a 12:00", "(601) 555-0104", "Admisión, entrega"),
    ("OFC-BOG-005", "Bogotá", "Kennedy", "Avenida Américas # 68-90", "8:00 a 18:00", "Cerrado", "(601) 555-0105", "Admisión, entrega, recaudo"),
    ("OFC-MED-001", "Medellín", "El Poblado", "Carrera 43A # 14-20", "7:30 a 18:30", "8:00 a 13:00", "(604) 555-0201", "Admisión, entrega, recaudo, recolección"),
    ("OFC-MED-002", "Medellín", "Centro", "Calle 49 # 50-21", "7:00 a 19:00", "8:00 a 14:00", "(604) 555-0202", "Admisión, entrega, recaudo"),
    ("OFC-MED-003", "Medellín", "Envigado", "Carrera 43A # 37 Sur-15", "8:00 a 17:00", "8:00 a 12:00", "(604) 555-0203", "Admisión, entrega"),
    ("OFC-MED-004", "Medellín", "Bello", "Diagonal 50 # 31-40", "8:00 a 17:30", "Cerrado", "(604) 555-0204", "Admisión, entrega, carga especial"),
    ("OFC-CAL-001", "Cali", "Centro", "Calle 11 # 5-60", "7:30 a 18:30", "8:00 a 13:00", "(602) 555-0301", "Admisión, entrega, recaudo, recolección"),
    ("OFC-CAL-002", "Cali", "Granada", "Avenida 6N # 25-30", "8:00 a 18:00", "8:00 a 12:00", "(602) 555-0302", "Admisión, entrega"),
    ("OFC-CAL-003", "Cali", "Sur", "Carrera 100 # 14-50", "8:00 a 17:30", "Cerrado", "(602) 555-0303", "Admisión, entrega, recaudo"),
    ("OFC-BAQ-001", "Barranquilla", "Norte", "Carrera 53 # 82-86", "7:30 a 18:00", "8:00 a 13:00", "(605) 555-0401", "Admisión, entrega, recaudo, recolección"),
    ("OFC-BAQ-002", "Barranquilla", "Centro", "Calle 34 # 43-31", "8:00 a 17:30", "8:00 a 12:00", "(605) 555-0402", "Admisión, entrega"),
    ("OFC-CTG-001", "Cartagena", "Bocagrande", "Avenida San Martín # 8-40", "8:00 a 18:00", "8:00 a 13:00", "(605) 555-0501", "Admisión, entrega, recaudo"),
    ("OFC-BGA-001", "Bucaramanga", "Cabecera", "Carrera 33 # 48-20", "7:30 a 18:00", "8:00 a 13:00", "(607) 555-0601", "Admisión, entrega, recaudo, recolección"),
    ("OFC-PEI-001", "Pereira", "Circunvalar", "Avenida Circunvalar # 6-40", "8:00 a 18:00", "8:00 a 12:00", "(606) 555-0701", "Admisión, entrega, recaudo"),
    ("OFC-MZL-001", "Manizales", "Centro", "Carrera 23 # 21-30", "8:00 a 17:30", "8:00 a 12:00", "(606) 555-0801", "Admisión, entrega"),
    ("OFC-CUC-001", "Cúcuta", "Centro", "Avenida 5 # 10-22", "7:30 a 17:30", "8:00 a 12:00", "(607) 555-0901", "Admisión, entrega, recaudo"),
    ("OFC-SMR-001", "Santa Marta", "El Rodadero", "Carrera 1 # 11-45", "8:00 a 17:00", "8:00 a 12:00", "(605) 555-1001", "Admisión, entrega"),
    ("OFC-VVC-001", "Villavicencio", "Centro", "Calle 38 # 30-15", "8:00 a 17:30", "8:00 a 12:00", "(608) 555-1101", "Admisión, entrega, recaudo"),
    ("OFC-PSO-001", "Pasto", "Centro", "Calle 18 # 25-40", "8:00 a 17:00", "Cerrado", "(602) 555-1201", "Admisión, entrega"),
    ("OFC-LET-001", "Leticia", "Principal", "Carrera 11 # 8-25", "8:00 a 16:00", "Cerrado", "(608) 555-1301", "Admisión, entrega por vía aérea"),
]

OFICINAS: Workbook = {
    "Oficinas": (
        ["Código", "Ciudad", "Nombre de la oficina", "Dirección", "Horario lunes a viernes", "Horario sábado", "Teléfono", "Servicios disponibles"],
        [list(row) for row in _OFFICES],
    )
}

# ---------------------------------------------------------------------------
# Tarifario referencial
# ---------------------------------------------------------------------------
# Precio base (0-1 kg, Estandar) por ruta, y dias habiles estimados Estandar.
_BASE_PRICE = {
    ("Bogotá", "Barranquilla"): 14500, ("Bogotá", "Cartagena"): 14900, ("Bogotá", "Leticia"): 31500,
    ("Bogotá", "Pasto"): 17800, ("Bogotá", "Bucaramanga"): 11200, ("Bogotá", "Cúcuta"): 13600,
    ("Medellín", "Barranquilla"): 13800, ("Medellín", "Cartagena"): 14200, ("Medellín", "Leticia"): 33200,
    ("Medellín", "Pasto"): 18900, ("Medellín", "Bucaramanga"): 11900, ("Medellín", "Cúcuta"): 13100,
    ("Cali", "Barranquilla"): 15600, ("Cali", "Cartagena"): 16000, ("Cali", "Leticia"): 34800,
    ("Cali", "Pasto"): 12400, ("Cali", "Bucaramanga"): 14300, ("Cali", "Cúcuta"): 16100,
}
_STANDARD_DAYS = {
    "Barranquilla": "4 a 6", "Cartagena": "4 a 6", "Leticia": "7 a 10",
    "Pasto": "5 a 7", "Bucaramanga": "3 a 5", "Cúcuta": "4 a 6",
}
_WEIGHT_TIERS = [("0 a 1", 1.0), ("1 a 5", 1.6), ("5 a 10", 2.4)]
_EXPRESS_FACTOR = 1.45
_EXPRESS_DAYS = "2 a 3"


def _round_100(value: float) -> int:
    return int(round(value / 100.0)) * 100


def tariff_price(origin: str, destination: str, tier_label: str, service: str) -> int:
    """Precio de una fila del tarifario -- usado tambien para escribir las
    respuestas esperadas de rag_golden.yaml a partir de la misma formula."""
    factor = dict(_WEIGHT_TIERS)[tier_label]
    price = _BASE_PRICE[(origin, destination)] * factor
    if service == "Express":
        price *= _EXPRESS_FACTOR
    return _round_100(price)


def _tariff_rows() -> list[list]:
    rows: list[list] = []
    for (origin, destination), _base in _BASE_PRICE.items():
        # Leticia es zona de dificil acceso: solo Estandar (ver el articulo
        # de entrega en zonas rurales).
        services = ["Estándar"] if destination == "Leticia" else ["Estándar", "Express"]
        for tier_label, _factor in _WEIGHT_TIERS:
            for service in services:
                price = tariff_price(origin, destination, tier_label, "Express" if service == "Express" else "Estándar")
                days = _EXPRESS_DAYS if service == "Express" else _STANDARD_DAYS[destination]
                rows.append([origin, destination, tier_label, service, price, days])
    return rows


_SURCHARGES = [
    ["R-01", "Zona de difícil acceso", "18% del flete", "Municipios de cobertura Ocurre o Aliado, y destinos por vía aérea o fluvial"],
    ["R-02", "Seguro de envío", "1,2% del valor declarado, mínimo $2.800", "Opcional; habilita la cobertura completa"],
    ["R-03", "Cambio de dirección en la misma ciudad", "$4.500", "Un solo cambio por envío, antes del reparto"],
    ["R-04", "Reexpedición a otra ciudad", "Tarifa nueva desde la ciudad actual", "El tiempo de entrega vuelve a contar desde cero"],
    ["R-05", "Devolución al remitente", "70% de la tarifa original", "Sin costo si el error fue de la empresa"],
    ["R-06", "Recolección a domicilio de 1 a 9 paquetes", "$3.500 por visita", "Gratis desde 10 paquetes"],
    ["R-07", "Visita de recolección fallida", "$3.500", "Cuando el mensajero llega y no hay paquetes"],
    ["R-08", "Carga especial (más de 30 kg o lado mayor de 150 cm)", "Tarifa por cotización", "Consultar con el área comercial"],
]

TARIFARIO: Workbook = {
    "Tarifas": (
        ["Origen", "Destino", "Rango de peso (kg)", "Servicio", "Precio (COP)", "Tiempo estimado (días hábiles)"],
        _tariff_rows(),
    ),
    "Recargos": (
        ["Código", "Concepto", "Valor", "Condición"],
        _SURCHARGES,
    ),
}

# ---------------------------------------------------------------------------
# Catalogo de codigos de novedad
# ---------------------------------------------------------------------------
_NOVELTIES = [
    # codigo, descripcion, responsable, accion, plazo horas, notifica
    ["N-01", "Dirección incompleta o inexistente", "Mensajero", "Llamar al destinatario y corregir la dirección", 24, "Sí"],
    ["N-02", "Dirección en zona de difícil acceso", "Coordinador de ruta", "Reprogramar con entrega en oficina o vía especial", 48, "Sí"],
    ["N-03", "Teléfono del destinatario errado", "Servicio al cliente", "Contactar al remitente para obtener un teléfono válido", 24, "Sí"],
    ["N-04", "Destinatario rechaza el paquete", "Mensajero", "Registrar el motivo e iniciar devolución", 24, "Sí"],
    ["N-05", "Destinatario ausente", "Mensajero", "Dejar aviso y reprogramar el intento", 24, "Sí"],
    ["N-06", "Paquete dañado detectado en bodega", "Jefe de bodega", "Fotografiar, abrir reclamación REC-01 y avisar al remitente", 12, "Sí"],
    ["N-07", "Paquete sin rótulo legible", "Jefe de bodega", "Identificar por escaneo de origen y reetiquetar", 12, "No"],
    ["N-08", "Contenido no coincide con lo declarado", "Supervisor de oficina", "Retener el envío y abrir inspección", 24, "Sí"],
    ["N-09", "Artículo prohibido detectado", "Supervisor de oficina", "Retener, no transportar y notificar al remitente", 12, "Sí"],
    ["N-10", "Cierre de vía o bloqueo", "Coordinador de ruta", "Reprogramar la ruta y avisar el retraso", 48, "No"],
    ["N-11", "Destinatario sin dinero para el recaudo", "Mensajero", "No entregar y reprogramar", 24, "Sí"],
    ["N-12", "Valor de recaudo en disputa", "Servicio al cliente", "Verificar el valor con el remitente", 24, "Sí"],
    ["N-13", "Restricción horaria del conjunto o edificio", "Mensajero", "Coordinar la ventana de entrega", 24, "Sí"],
    ["N-14", "Condición climática adversa", "Coordinador de ruta", "Suspender la ruta y reprogramar", 48, "No"],
    ["N-15", "Vuelo cancelado (destinos aéreos)", "Coordinador de ruta", "Reasignar a la siguiente salida aérea", 168, "Sí"],
    ["N-16", "Retención aduanera", "Comercio exterior", "Pedir documentos al remitente y tramitar con la aduana", 120, "Sí"],
    ["N-17", "Documento de identidad no coincide", "Mensajero", "No entregar y confirmar el autorizado", 24, "Sí"],
    ["N-18", "Entrega en oficina (Ocurre) pendiente de reclamar", "Oficina destino", "Mantener en custodia 10 días calendario", 240, "Sí"],
    ["N-19", "Envío duplicado en el sistema", "Soporte técnico", "Fusionar los registros y conservar la guía original", 48, "No"],
    ["N-20", "Error de digitación en el origen", "Oficina origen", "Corregir el dato y reexpedir sin costo", 24, "Sí"],
]

NOVEDADES: Workbook = {
    "Novedades": (
        ["Código", "Descripción", "Responsable", "Acción requerida", "Plazo máximo (horas)", "Notifica al cliente"],
        _NOVELTIES,
    )
}

# ---------------------------------------------------------------------------
# Cobertura de municipios (codigos DANE de capitales; la cobertura es inventada)
# ---------------------------------------------------------------------------
_COVERAGE = [
    ["11001", "Bogotá D.C.", "Bogotá D.C.", "Directa", "Lunes a sábado", "Sin restricciones"],
    ["05001", "Medellín", "Antioquia", "Directa", "Lunes a sábado", "Sin restricciones"],
    ["76001", "Cali", "Valle del Cauca", "Directa", "Lunes a sábado", "Sin restricciones"],
    ["08001", "Barranquilla", "Atlántico", "Directa", "Lunes a sábado", "Sin restricciones"],
    ["13001", "Cartagena", "Bolívar", "Directa", "Lunes a sábado", "Zona amurallada con ventana de entrega limitada"],
    ["68001", "Bucaramanga", "Santander", "Directa", "Lunes a sábado", "Sin restricciones"],
    ["54001", "Cúcuta", "Norte de Santander", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["66001", "Pereira", "Risaralda", "Directa", "Lunes a sábado", "Sin restricciones"],
    ["17001", "Manizales", "Caldas", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["47001", "Santa Marta", "Magdalena", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["50001", "Villavicencio", "Meta", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["52001", "Pasto", "Nariño", "Directa", "Lunes a viernes", "Sin entrega los sábados"],
    ["73001", "Ibagué", "Tolima", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["41001", "Neiva", "Huila", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["23001", "Montería", "Córdoba", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["63001", "Armenia", "Quindío", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["19001", "Popayán", "Cauca", "Aliado", "Martes y jueves", "Entrega con punto aliado"],
    ["20001", "Valledupar", "Cesar", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["15001", "Tunja", "Boyacá", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["18001", "Florencia", "Caquetá", "Aliado", "Martes y jueves", "Entrega con punto aliado"],
    ["85001", "Yopal", "Casanare", "Aliado", "Martes y jueves", "Entrega con punto aliado"],
    ["44001", "Riohacha", "La Guajira", "Aliado", "Martes y viernes", "Entrega con punto aliado"],
    ["27001", "Quibdó", "Chocó", "Ocurre", "Lunes a viernes", "Reclamar en oficina; recargo R-01"],
    ["70001", "Sincelejo", "Sucre", "Directa", "Lunes a viernes", "Sin restricciones"],
    ["88001", "San Andrés", "San Andrés y Providencia", "Ocurre", "Semanal", "Vía aérea; recargo R-01"],
    ["86001", "Mocoa", "Putumayo", "Aliado", "Martes y jueves", "Recargo R-01"],
    ["81001", "Arauca", "Arauca", "Aliado", "Martes y jueves", "Recargo R-01"],
    ["99001", "Puerto Carreño", "Vichada", "Ocurre", "Miércoles", "Vía aérea semanal; recargo R-01"],
    ["94001", "Inírida", "Guainía", "Ocurre", "Miércoles", "Vía aérea semanal; recargo R-01"],
    ["97001", "Mitú", "Vaupés", "Ocurre", "Miércoles", "Vía aérea semanal; recargo R-01"],
    ["95001", "San José del Guaviare", "Guaviare", "Aliado", "Martes y jueves", "Recargo R-01"],
    ["91001", "Leticia", "Amazonas", "Ocurre", "Lunes y jueves", "Vía aérea; solo servicio Estándar; recargo R-01"],
]

COBERTURA: Workbook = {
    "Cobertura": (
        ["Código DANE", "Municipio", "Departamento", "Tipo de cobertura", "Frecuencia de entrega", "Observaciones"],
        _COVERAGE,
    )
}

WORKBOOKS: dict[str, Workbook] = {
    "Directorio de oficinas": OFICINAS,
    "Tarifario referencial": TARIFARIO,
    "Catalogo de codigos de novedad": NOVEDADES,
    "Cobertura de municipios": COBERTURA,
}
