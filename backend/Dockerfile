FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libpcap for scapy raw-socket capture; curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpcap0.8 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# install dependencies first for layer caching
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install -e ".[net]"

# non-root runtime user
RUN useradd -m -u 10001 nettwin && chown -R nettwin:nettwin /app
USER nettwin

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
