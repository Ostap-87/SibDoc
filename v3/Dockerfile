# PostControl Suite (SibDoc) — полный образ (API + Web UI + CLI + OCR)
FROM python:3.12-slim

# Шрифты для PDF: DejaVu (латиница/кириллица) + WenQuanYi Zen Hei (иероглифы),
# Tesseract OCR (рус/англ/кит — модели в ./tessdata)
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        fonts-wqy-zenhei \
        tesseract-ocr \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/postcontrol

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY references ./references
COPY webui ./webui
COPY data ./data
COPY tools ./tools
# Языковые модели OCR (rus/eng/chi_sim) — офлайн, внутри образа
COPY tessdata ./tessdata
COPY run_cli.py .

# Работаем не от root: отдельный пользователь, права только на storage
RUN useradd --create-home --uid 10001 sibdoc \
    && mkdir -p storage/reports storage/uploads storage/runs \
    && chown -R sibdoc:sibdoc storage config/rules config/rates.yaml
USER sibdoc

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fs http://localhost:8000/api/health || exit 1

# Конфиг можно переопределить через PCC_CONFIG=/etc/postcontrol/system.yaml
# Пароли коннекторов и уведомлений — только переменными окружения (*_env в system.yaml)
ENV PCC_CONFIG=""

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
