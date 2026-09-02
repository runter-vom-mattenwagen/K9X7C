FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/opt/models \
    HF_HUB_DISABLE_TELEMETRY=1 \
    EMBED_MODEL=jinaai/jina-embeddings-v2-base-de

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fetch the model at build time: container start must not depend on the network.
RUN python -c "import os;from fastembed import TextEmbedding;TextEmbedding(os.environ['EMBED_MODEL'])" \
    && du -sh /opt/models

COPY app/ /app/

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
