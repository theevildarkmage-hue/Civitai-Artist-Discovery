FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu passwd ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1024 discovery \
    && useradd --uid 1024 --gid discovery --create-home --shell /usr/sbin/nologin discovery

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CAD_CONTAINER=1 CIVITAI_HISTORY_DATA_DIR=/data HOME=/home/discovery
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py ./
COPY discovery/ ./discovery/
COPY static/ ./static/
COPY --chmod=755 docker_init.bash /usr/local/bin/discovery-init

# Root is used only for account/ownership initialization. The entrypoint execs gosu.
USER root
EXPOSE 8765
ENTRYPOINT ["/usr/local/bin/discovery-init"]
CMD ["python", "server.py", "--stable", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
