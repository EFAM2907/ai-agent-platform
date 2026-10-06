# Política de Envíos Demorados y Apertura de Investigación

Un envío se considera **demorado** cuando pasa demasiado tiempo sin un evento nuevo (ver `envios.md` para la lista de estados). Los umbrales dependen de la zona:

- **Zona urbana / ciudades principales:** 2 días hábiles sin evento nuevo = seguimiento preventivo automático (sin acción del cliente). 3 días hábiles sin evento nuevo = se considera demorado.
- **Zona nacional / intermunicipal:** 3 días hábiles sin evento nuevo = seguimiento preventivo. 5 días hábiles sin evento nuevo = se considera demorado.
- **Zonas rurales o de difícil acceso:** se suman 2 días hábiles adicionales a los umbrales anteriores antes de considerarlo demorado, por la menor frecuencia de rutas.

**A partir de que un envío se considera demorado, el cliente (o un agente en su nombre) puede solicitar la apertura de una investigación formal.** Abrir una investigación:

1. Congela el envío para trazabilidad prioritaria — un analista de logística revisa manualmente el último centro que reportó el paquete.
2. Genera un número de caso (`Claim #`) que el cliente puede usar para hacer seguimiento.
3. Tiene un SLA de respuesta de 3 días hábiles para dar un diagnóstico: el paquete aparece y se reactiva el tránsito normal, o se determina que aplica el procedimiento de `paquetes_perdidos.md`.

**Antes de abrir una investigación, siempre se debe confirmar con el cliente** — abrir una investigación no es una acción automática ni silenciosa; es una acción sensible porque congela el envío y notifica al área de logística, así que el agente (humano o AI) debe explicar la situación y pedir confirmación explícita antes de ejecutarla.

Si el envío lleva más de 10 días hábiles sin movimiento y ya se abrió una investigación sin resultado, ver `paquetes_perdidos.md` para el siguiente paso.
