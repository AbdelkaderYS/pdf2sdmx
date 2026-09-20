# Two stages: dependencies in the first, a slim runtime in the second.
#
# Everything the app needs at run time is baked in, because a host that scales to zero
# starts from a cold image on the next visit and a download there is time the visitor
# waits. That makes the image large; pulling it from a registry beside the machine is
# still faster than fetching 230 MB of models from elsewhere.

FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends ghostscript \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /build

COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache --extra-index-url https://download.pytorch.org/whl/cpu ".[ml]"


FROM python:3.12-slim AS runtime

# ghostscript for the classic Camelot flavour, libglib for headless opencv.
RUN apt-get update && apt-get install -y --no-install-recommends ghostscript libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src ./src
COPY app.py ./
COPY mapping ./mapping
# The vocabulary, the sample, the sources list and the cached code lists. .dockerignore
# keeps the source reports out: they are heavy and they are the publisher's.
COPY data ./data
RUN chown -R app:app /app/data

USER app

# The official SDMX 2.1 schemas and the table models, fetched once here rather than on
# every cold start. They land in the app user's home, which is why this follows USER.
RUN python -c "import sdmx; sdmx.install_schemas(version='2.1')"
RUN python -c "\
import sys; sys.path.insert(0, 'src');\
from transformers import AutoModelForObjectDetection;\
AutoModelForObjectDetection.from_pretrained('microsoft/table-transformer-detection');\
AutoModelForObjectDetection.from_pretrained('microsoft/table-transformer-structure-recognition');\
print('table models cached')"

# A host that assigns a port passes it in PORT; 7860 is the default elsewhere.
ENV PORT=7860
EXPOSE 7860

CMD ["python", "app.py"]
