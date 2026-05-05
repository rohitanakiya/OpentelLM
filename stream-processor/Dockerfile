FROM python:3.12-slim

WORKDIR /app

# kafka-python tooling for the healthcheck script
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Healthcheck: verify the metrics-aggregator group has at least one
# committed offset against the llm.requests topic. If it never has, either
# Kafka is down or the consumer never came up.
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
  CMD python /app/healthcheck.py || exit 1

CMD ["python", "main.py"]
