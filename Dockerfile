FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev python3-dev \
    # Chromium for Playwright (amd64 Docker; Pi uses system chromium instead)
    chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# /srv holds the project root so "from app.x import y" imports work
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 playwright install-deps chromium

RUN useradd -m -u 1000 appuser
USER appuser

# Copy app/ as the 'app' package under /srv
COPY app/ app/

# CWD = /srv/app so Jinja2 "templates/" and "static/" resolve correctly;
# PYTHONPATH = /srv so "from app.xxx import yyy" finds /srv/app/xxx.py
ENV PYTHONPATH=/srv
WORKDIR /srv/app

EXPOSE 8766

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8766"]
