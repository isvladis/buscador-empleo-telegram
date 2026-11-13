FROM python:3.12-slim

WORKDIR /app

# build-essential: algunas dependencias (p.ej. tls-client) instalan desde sdist si no
# hay wheel para la plataforma exacta del host; sin compilador, pip fallaría ahí.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# JOBSPY_DATA_DIR: los JSON persistentes (ofertas, estado, config, caché de
# geocodificación, modelo entrenado) se leen/escriben aquí en vez de junto al código,
# para que docker-compose.yml pueda montar un único volumen y sobrevivan a recrear el
# contenedor. No hace falta sembrar el directorio: el servicio sabe arrancar sin ellos
# (config con los valores por defecto, y perfiles.db se crea al primer uso).
RUN mkdir -p /app/data
ENV JOBSPY_DATA_DIR=/app/data

# Sin puerto desde MP5: el servicio no escucha en la red (ver app.py). Forma exec del CMD a
# propósito: así python es el PID 1 y recibe el SIGTERM de `docker stop`, que app.py atiende.
CMD ["python", "app.py"]
