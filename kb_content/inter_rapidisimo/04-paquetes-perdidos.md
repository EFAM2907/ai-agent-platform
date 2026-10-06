# Procedimiento para Envíos Perdidos

Un envío **nunca** se declara perdido directamente a partir de una pregunta del cliente — siempre pasa primero por el proceso de investigación descrito en `retrasos.md`. La secuencia completa es:

1. El envío supera el umbral de días sin movimiento correspondiente a su zona → se considera demorado.
2. Se abre una investigación formal (con confirmación previa del cliente) → SLA de 3 días hábiles para diagnóstico.
3. Si el analista de logística no logra ubicar el paquete en ningún centro de distribución dentro de ese SLA, **y** ya pasaron 10 días hábiles totales sin ningún evento nuevo, el envío se declara oficialmente **perdido**.

Una vez declarado perdido:

- Se notifica al remitente y al destinatario con el resultado de la investigación.
- Se abre (o se actualiza, si ya existía por la investigación) una reclamación de tipo "pérdida" en `reclamaciones.md`.
- Aplica indemnización según `indemnizaciones.md` — con valor declarado y seguro, se indemniza hasta el valor declarado; sin seguro, se indemniza según la tarifa base por peso.

No existe un procedimiento de "reenvío automático" para paquetes perdidos — cualquier reposición del contenido es una decisión comercial aparte entre el remitente y el destinatario, no una obligación de Inter Rapidísimo más allá de la indemnización correspondiente.
