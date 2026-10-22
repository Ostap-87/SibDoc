# PostControl Suite — полный образ (API + Web UI + CLI + OCR)
FROM python:3.12-slim

# Шрифты с кириллицей для PDF-отчётов + Tesseract OCR (рус/англ/кит)
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/postcontrol

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY references ./references
COPY webui ./webui
COPY data ./data
# Языковые модели OCR (rus/eng/chi_sim) — офлайн, внутри образа
COPY tessdata ./tessdata
COPY run_cli.py .

RUN mkdir -p storage/reports

EXPOSE 8000

# Конфиг можно переопределить через PCC_CONFIG=/etc/postcontrol/system.yaml
ENV PCC_CONFIG=""

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
