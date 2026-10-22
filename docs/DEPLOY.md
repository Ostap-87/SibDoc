# Развёртывание на сервере

Целевая схема: один Linux-сервер с Docker, сервис на порту 8000,
доступ для команды 5–6 человек по `http://server:8000`.

---

## 1. Требования

- Docker 20.10+ (или Docker + docker compose plugin);
- 1 CPU / 1 GB RAM достаточно (прогон — секунды);
- открытый порт 8000 (или любой другой, см. ниже).

## 2. Установка (5 минут)

```bash
# 1. скопируйте папку PostControl Suite на сервер
cd "PostControl Suite"

# 2. соберите образ
docker build -t postcontrol-suite .

# 3. запустите (config/references/storage — снаружи, редактируются без пересборки)
docker run -d -p 8000:8000 \
  -v $(pwd)/config:/srv/postcontrol/config \
  -v $(pwd)/references:/srv/postcontrol/references \
  -v $(pwd)/storage:/srv/postcontrol/storage \
  --name postcontrol --restart unless-stopped \
  postcontrol-suite

# 4. проверьте
curl http://localhost:8000/api/health
# {"status":"ok","product":"PostControl Suite"}
```

Альтернатива — `docker compose up -d` (то же самое, compose-файл приложен).

Откройте команде: **http://server:8000** — интерфейс готов к работе.

## 3. Что лежит снаружи контейнера (volumes)

| Папка на сервере | Зачем | Как обновлять |
|---|---|---|
| `config/` | system.yaml + rules/*.yaml | править на месте; rules применяются сразу, system.yaml — после `docker restart postcontrol` |
| `references/` | справочники HS-кодов, CDE | заменить CSV — применяется на следующий прогон |
| `storage/` | Excel/PDF-отчёты | забрать/почистить в любой момент |

## 4. Типовые операции

**Обновить параметры проверки** — веб-интерфейс, шаг 2, или правка
`config/rules/*.yaml` на сервере. Перезапуск не нужен.

**Обновить справочник HS-кодов** — заменить `references/hs_codes.csv`.

**Обновить код продукта** —
```bash
docker build -t postcontrol-suite . && docker rm -f postcontrol && docker run ... (как в п.2)
```

**Логи** — `docker logs -f postcontrol`.

**Бэкап** — архивировать `config/`, `references/`, `storage/`.

## 5. Безопасность (важно)

- Встроенная авторизация отсутствует. Размещайте сервис **внутри корпоративной сети**
  или за reverse-proxy с Basic-Auth/SSO (nginx example ниже).
- Файлы содержат коммерческие данные (цены, контракты) — ограничьте доступ к порту.

Минимальный nginx-фрагмент:
```nginx
location /postcontrol/ {
    auth_basic "postcontrol";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://127.0.0.1:8000/;
}
```

## 6. Запуск без Docker (вариант)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
# или без сервера: python run_cli.py --wide ... --purchase ...
```

## 7. Проверка после установки (эталонный прогон)

```bash
docker exec postcontrol python run_cli.py \
  --wide data/wide_table.xlsx --purchase data/purchase.xlsx
# ожидается: price 32 OK / 5 ISSUE; contracts 2 ISSUE; DQ: 1 ERROR
```

Если числа совпали — установка корректна, можно работать.
