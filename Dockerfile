FROM python:3.12-slim

# Installazione dipendenze di sistema aggiornate per Debian Trixie
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia e installazione dei requisiti Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installazione dei browser di Playwright (Chromium)
RUN playwright install --with-deps chromium

COPY . .

# Comando per avviare FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
