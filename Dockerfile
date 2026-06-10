# Образ с предустановленным Chromium и системными зависимостями Playwright
# (версия совпадает с playwright из requirements — 1.60.x).
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения. Секреты и профиль НЕ запекаем в образ — они монтируются
# томами в рантайме (service_account.json, .env, browser_profile/).
COPY utils.py sheets.py simple_run.py run.py ./

# На сервере работаем headless; первичный логин (с 2FA) прогревает
# browser_profile/ один раз вне контейнера или через одноразовую сессию.
ENV HEADLESS=true \
    PYTHONUNBUFFERED=1

CMD ["python", "run.py"]
