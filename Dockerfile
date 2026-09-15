# Multi-stage build: dependencies in one stage, slim runtime in the next.

FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends ghostscript && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /build

COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache --extra-index-url https://download.pytorch.org/whl/cpu ".[ml]"


FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends ghostscript && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src ./src
COPY app.py ./
COPY mapping ./mapping

USER app
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')"

CMD ["python", "app.py"]
