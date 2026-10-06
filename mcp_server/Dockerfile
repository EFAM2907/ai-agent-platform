# Imagen liviana a proposito: este servidor es un proxy delgado sobre
# la API REST (ver mcp_server/server.py), no importa SQLAlchemy,
# asyncpg ni cryptography -- esas son las dependencias mas pesadas de
# la imagen principal de la API, y este contenedor no las necesita.
FROM python:3.12-slim

WORKDIR /app

COPY mcp_server/requirements.txt ./mcp_server/requirements.txt
RUN pip install --no-cache-dir -r mcp_server/requirements.txt

COPY mcp_server/ ./mcp_server/

EXPOSE 8100

CMD ["python", "-m", "mcp_server.server"]
