# Consulta y Estados de un Envío

Cada envío de Inter Rapidísimo pasa por una serie de estados, registrados como eventos con fecha, hora y ciudad/centro de distribución donde ocurrieron:

1. **Admitido** — el paquete fue recibido en una oficina o recogido por un mensajero.
2. **En bodega de origen** — clasificado y a la espera de salir hacia el siguiente centro.
3. **En tránsito** — en ruta entre centros de distribución (puede tener varios eventos intermedios, uno por cada centro que atraviesa).
4. **En bodega destino** — llegó a la ciudad de entrega y está pendiente de asignación a reparto.
5. **En reparto** — un mensajero ya lo tiene asignado para entregarlo ese día.
6. **Entregado** — entrega confirmada, con nombre y documento de quien recibió.
7. **Novedad** — no se pudo continuar el proceso normal (dirección incompleta, destinatario ausente, paquete dañado detectado en bodega, etc.). Un envío con novedad requiere una acción explícita para seguir avanzando.

El "último movimiento" de un envío es la fecha y el estado de su evento más reciente. Cuando un cliente pregunta dónde está su paquete, la respuesta siempre debe incluir: el estado actual, la ciudad del último evento y la fecha de ese evento — no solo el estado.

**Importante:** no todos los envíos generan un evento nuevo todos los días. Un envío en tránsito entre ciudades lejanas puede pasar 1-2 días hábiles sin un evento nuevo sin que eso sea anormal — es tiempo de viaje en ruta, no una falla. Ver `retrasos.md` para los umbrales exactos que distinguen un tiempo de tránsito normal de un envío efectivamente demorado.
