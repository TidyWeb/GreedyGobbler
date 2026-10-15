FROM python:3.11-slim

ARG INSTALL_BROWSER=false
ARG INSTALL_COMPRESSION=false
ARG PRELOAD_TOKENIZERS=true

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5001 \
    SAVE_DIR=/data/saves \
    TEMP_DIR=/data/temp \
    TIKTOKEN_CACHE_DIR=/opt/gobbler/tokenizers

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    poppler-utils \
    tesseract-ocr

WORKDIR /app
COPY requirements.txt requirements-browser.txt requirements-compression.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN if [ "$INSTALL_COMPRESSION" = "true" ]; then \
        pip install --no-cache-dir -r requirements-compression.txt; \
    fi

COPY app.py converter.py ./
COPY pipeline ./pipeline
COPY static ./static

# Tokenizer vocabulary files are small, static data. They are fetched only
# while building the image and are not masked by the persistent /data volume.
RUN mkdir -p /opt/gobbler/tokenizers /data/saves /data/temp \
    && if [ "$PRELOAD_TOKENIZERS" = "true" ]; then \
        TIKTOKEN_CACHE_DIR=/opt/gobbler/tokenizers python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"; \
    fi

RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
        pip install --no-cache-dir -r requirements-browser.txt && crawl4ai-setup; \
    fi

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin gobbler \
    && mkdir -p /home/gobbler/.cache \
    && if [ -d /root/.cache/ms-playwright ]; then cp -a /root/.cache/ms-playwright /home/gobbler/.cache/; fi \
    && chown -R gobbler:gobbler /data /opt/gobbler /home/gobbler /app

VOLUME ["/data"]
EXPOSE 5001
USER gobbler
CMD ["python", "app.py"]
