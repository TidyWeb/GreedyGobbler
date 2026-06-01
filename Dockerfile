FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

RUN mkdir -p /home/Phil/Downloads/GreedyGobbler /home/Phil/projects/GreedyGobbler/temp

EXPOSE 5001

CMD ["python", "app.py"]
