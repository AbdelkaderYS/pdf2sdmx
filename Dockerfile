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

# The official SDMX 2.1 schemas, so the app can state whether its output conforms.
# They land in the app user's data directory, which is why this runs after USER.
RUN python -c "import sdmx; sdmx.install_schemas(version='2.1')"

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')"

CMD ["python", "app.py"]
