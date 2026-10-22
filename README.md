# SibDoc (PostControl Suite)

Пост-контроль импортных таможенных деклараций: сверка CD с данными SAP
по методике Post Control Report Handbook. Проверки цен, HS-кодов,
инкотермсов, элементов декларации, контрактов; DQ-детекция; отчёты Excel/PDF.

Запуск: pip install -r requirements.txt && uvicorn app.server:app --port 8000
Конфиг: config/system.yaml (источники, коннекторы, режим входа)
Правила: config/rules/*.yaml — без рестарта. Эталон: verify.sh
Рабочие данные (storage/) в репозиторий не коммитятся.
