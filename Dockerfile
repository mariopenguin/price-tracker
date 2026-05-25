FROM python:3.11-slim-bullseye

RUN apt-get update && apt-get install -y \
    gcc libffi-dev python3-dev \
    libxml2-dev libxslt1-dev \
    chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# /srv holds the project root so "from app.x import y" imports work
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
