FROM python:3.12-slim

# Chromium is the fallback renderer for career pages that build their job list
# in JavaScript (Cognigy, telli's old board, join.com).
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium ca-certificates && rm -rf /var/lib/apt/lists/*
ENV CHROME_BIN=/usr/bin/chromium

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
