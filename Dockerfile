# База python:3.11-slim (тянется из Docker Hub) + Chromium ставится Playwright'ом.
# Так надёжнее, чем mcr.microsoft.com/playwright (тот registry недоступен/медленный
# из РФ — образ деплоится на российский VPS).
FROM python:3.11-slim-bookworm

WORKDIR /app

# Зависимости Python + Chromium с системными библиотеками (--with-deps ставит apt-пакеты).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

# Код приложения. Секреты и профиль НЕ запекаем — монтируются томами в рантайме
# (service_account.json, .env, browser_profile/).
COPY utils.py sheets.py simple_run.py run.py ./

# На сервере работаем headless; первичный логин (с 2FA) прогревает browser_profile/.
ENV HEADLESS=true \
    PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
