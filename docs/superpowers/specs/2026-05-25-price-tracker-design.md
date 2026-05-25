# Price Tracker — Documento de Diseño

**Fecha:** 2026-05-25  
**Estado:** Aprobado  
**Plataforma destino:** Raspberry Pi 3B · ARM 32-bit (armv7l) · Raspbian Bullseye  
**Despliegue:** Docker + Portainer · Puerto 8766

---

## 1. Resumen

Aplicación web personal para monitorizar el precio de productos en internet. El usuario aporta una URL y la app comprueba periódicamente si el precio ha subido o bajado, guardando un histórico diario. Incluye notificaciones por Telegram y un bot que replica las funcionalidades principales.

---

## 2. Arquitectura

### Patrón: Monolito con scheduler interno

Un único contenedor Docker con cuatro componentes internos:

```
┌─────────────────────────────────────────────────────┐
│  Docker Container: price-tracker (puerto 8766)      │
│                                                     │
│  ┌─────────────┐   ┌──────────────────────────┐    │
│  │  FastAPI    │   │  APScheduler             │    │
│  │  + Jinja2   │   │  (scraping cada N horas) │    │
│  │  Puerto 8766│   └────────────┬─────────────┘    │
│  └──────┬──────┘                │                  │
│         │                  ┌────▼──────────┐       │
│         └──────────────────►  SQLite       │       │
│                            │  (volumen)    │       │
│  ┌─────────────┐           └───────────────┘       │
│  │  Telegram   │                                   │
│  │  Bot thread │                                   │
│  └─────────────┘                                   │
└─────────────────────────────────────────────────────┘
```

### Stack técnico

| Componente | Tecnología | Motivo |
|---|---|---|
| Web framework | FastAPI + Jinja2 | SSR sin build step, consistente con apps existentes |
| ASGI server | `uvicorn` (sin `[standard]`) | uvloop no compila en ARMv7 32 bits |
| Scheduler | APScheduler | In-process, cero contenedores extra |
| ORM | SQLAlchemy + aiosqlite | Async nativo con FastAPI |
| Scraping (conocido) | requests + BeautifulSoup4 | Ligero, sin navegador |
| Scraping (genérico) | selenium + chromium del sistema | Playwright no soporta ARMv7 32 bits |
| Bot | python-telegram-bot | Hilo daemon |
| Auth | passlib + python-jose | JWT en cookies httponly |
| Frontend | Jinja2 + Chart.js (CDN) | Sin build step |

---

## 3. Modelo de datos

### Tabla: `users`
```
id              INTEGER PRIMARY KEY
email           TEXT UNIQUE NOT NULL
username        TEXT UNIQUE NOT NULL
password_hash   TEXT NOT NULL
is_admin        BOOLEAN DEFAULT FALSE
telegram_chat_id TEXT NULLABLE  -- vinculado desde el bot
created_at      DATETIME DEFAULT NOW
```

### Tabla: `invite_codes`
```
id          INTEGER PRIMARY KEY
code        TEXT UNIQUE NOT NULL  -- UUID
created_by  INTEGER FK users.id
used_by     INTEGER FK users.id NULLABLE
created_at  DATETIME DEFAULT NOW
used_at     DATETIME NULLABLE
```

### Tabla: `products`
```
id                  INTEGER PRIMARY KEY
user_id             INTEGER FK users.id ON DELETE CASCADE
url                 TEXT NOT NULL
name                TEXT NOT NULL
image_url           TEXT NULLABLE
current_price       DECIMAL(10,2) NULLABLE
currency            TEXT DEFAULT 'EUR'
last_checked_at     DATETIME NULLABLE
created_at          DATETIME DEFAULT NOW
is_active           BOOLEAN DEFAULT TRUE
notify_on_drop      BOOLEAN DEFAULT FALSE
target_price        DECIMAL(10,2) NULLABLE  -- avisa si precio <= este valor
target_percentage   DECIMAL(5,2) NULLABLE   -- avisa si baja >= N%
```

**Constraint:** máximo 100 productos activos por usuario, validado en capa de negocio.

### Tabla: `price_history`
```
id          INTEGER PRIMARY KEY
product_id  INTEGER FK products.id ON DELETE CASCADE
price       DECIMAL(10,2) NOT NULL
recorded_at DATETIME DEFAULT NOW
```

Un registro por cada comprobación exitosa.

---

## 4. Sistema de scraping

### Scrapers dedicados (requests + BeautifulSoup)
- `amazon.py` — Amazon.es
- `ebay.py` — eBay.es
- `mediamarkt.py` — MediaMarkt.es
- `pccomponentes.py` — PCComponentes.com
- `elcorteingles.py` — El Corte Inglés
- `generic.py` — Fallback para cualquier otra URL

### Lógica del fallback genérico (dos niveles)
1. **requests + BeautifulSoup:** busca `[itemprop="price"]`, JSON-LD `schema.org/Product`, meta tags Open Graph. Si encuentra precio → devuelve.
2. **Selenium + Chromium:** se instancia solo si el nivel 1 falla o la página requiere JS. Se abre, extrae el precio y **se cierra inmediatamente** para liberar memoria.

### Scheduling (APScheduler)
- Intervalo: 6 horas por defecto (configurable con `CHECK_INTERVAL_HOURS`)
- Procesamiento **en serie** (no paralelo) para no saturar la Pi
- Productos con Selenium se procesan al final del batch, de uno en uno
- Si falla tras 2 reintentos → registra el intento fallido, continúa con el siguiente

### Lógica de notificación (tras cada scrape exitoso)
```
si nuevo_precio < precio_anterior  Y  notify_on_drop == True  → notificar
si nuevo_precio <= target_price  Y  target_price IS NOT NULL  → notificar
si bajada% >= target_percentage  Y  target_percentage IS NOT NULL → notificar
```
Las notificaciones se envían por Telegram si `telegram_chat_id` está vinculado.

---

## 5. Interfaz web

### Rutas

| Ruta | Descripción | Auth |
|---|---|---|
| `GET /login` | Formulario login | No |
| `POST /login` | Procesa login, emite cookie JWT | No |
| `GET /register?code=XXX` | Formulario registro con código | No |
| `POST /register` | Crea usuario, invalida código | No |
| `GET /` | Dashboard: lista productos + precios | Sí |
| `GET /product/add` | Formulario añadir URL | Sí |
| `POST /product/add` | Extrae metadatos y guarda producto | Sí |
| `GET /product/{id}` | Detalle + gráfica histórico + alertas | Sí |
| `POST /product/{id}/alerts` | Actualiza configuración alertas | Sí |
| `POST /product/{id}/delete` | Borra producto y su histórico | Sí |
| `GET /settings` | Vincular Telegram, cambiar contraseña | Sí |
| `GET /admin` | Generar códigos, ver usuarios | Admin |

### UX
- Al añadir un producto: la app extrae nombre, imagen y precio inicial antes de guardar (feedback inmediato al usuario)
- Dashboard: flecha verde ▼ / roja ▲ indicando variación respecto a la comprobación anterior
- Gráfica de histórico: Chart.js (cargado desde CDN, sin build step)
- Límite de 100 productos: mensaje de error claro al intentar añadir el 101º

---

## 6. Bot de Telegram

### Flujo de registro
1. Usuario hace `/start` → bot solicita código de invitación
2. Usuario envía código → bot valida y vincula `telegram_chat_id` a la cuenta web
3. Confirmación: "Cuenta vinculada. Ya puedes usar el bot."

### Comandos

| Comando | Descripción |
|---|---|
| `/start` | Bienvenida + inicio registro |
| `/lista` | Lista todos los productos con precio actual |
| `/add <url>` | Añade nuevo producto para trackear |
| `/precio <id>` | Consulta precio actual de un producto |
| `/borrar <id>` | Elimina un producto |
| `/alertas <id>` | Muestra y permite modificar alertas del producto |
| `/ayuda` | Lista de comandos disponibles |

### Formato de notificación proactiva
```
📉 Nombre del producto
Precio anterior: 89,99 €
Precio actual:   74,99 € (-16,7%)
🔗 Ver producto: https://...
```

---

## 7. Despliegue en Portainer

### Estructura del repositorio
```
price-tracker/
├── app/
│   ├── main.py
│   ├── models.py
│   ├── database.py
│   ├── auth.py
│   ├── scheduler.py
│   ├── bot.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── amazon.py
│   │   ├── ebay.py
│   │   ├── mediamarkt.py
│   │   ├── pccomponentes.py
│   │   ├── elcorteingles.py
│   │   └── generic.py
│   ├── routers/
│   │   ├── auth.py
│   │   ├── products.py
│   │   ├── admin.py
│   │   └── settings.py
│   └── templates/
│       ├── base.html
│       ├── login.html
│       ├── register.html
│       ├── dashboard.html
│       ├── product_detail.html
│       ├── product_add.html
│       ├── settings.html
│       └── admin.html
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

### Variables de entorno
```env
SECRET_KEY=<clave secreta larga para JWT>
DATABASE_URL=sqlite:////data/db.sqlite3
TELEGRAM_BOT_TOKEN=<token del bot de BotFather>
CHECK_INTERVAL_HOURS=6
FIRST_ADMIN_EMAIL=mariodam123@gmail.com
FIRST_ADMIN_PASSWORD=<contraseña inicial del admin>
```

### Dockerfile (ARM 32-bit compatible)
```dockerfile
FROM python:3.11-slim-bullseye
RUN apt-get update && apt-get install -y \
    gcc libffi-dev python3-dev \
    chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
VOLUME /data
EXPOSE 8766
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8766"]
```

### docker-compose.yml
```yaml
version: '3.8'
services:
  price-tracker:
    build: .
    container_name: price-tracker
    restart: unless-stopped
    ports:
      - "8766:8766"
    volumes:
      - price_tracker_data:/data
    environment:
      - SECRET_KEY=${SECRET_KEY}
      - DATABASE_URL=${DATABASE_URL}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - CHECK_INTERVAL_HOURS=${CHECK_INTERVAL_HOURS}
      - FIRST_ADMIN_EMAIL=${FIRST_ADMIN_EMAIL}
      - FIRST_ADMIN_PASSWORD=${FIRST_ADMIN_PASSWORD}
volumes:
  price_tracker_data:
```

> El `.env` **no se incluye en el repo** (contiene secretos). Las variables se configuran en Portainer UI al hacer el deploy.

### Pasos en Portainer
1. Portainer → Stacks → Add stack → Repository
2. URL del repo GitHub
3. Reference: `refs/heads/main`
4. Compose path: `docker-compose.yml`
5. Sección "Environment variables" → añadir las 6 variables del `.env.example`
6. Deploy → `http://192.168.1.50:8766`

---

## 8. Consideraciones ARM 32-bit

| Problema conocido | Solución aplicada |
|---|---|
| `uvloop` no compila | Usar `uvicorn` sin `[standard]` |
| Playwright no soporta ARMv7 | Usar selenium + chromium del sistema (`apt`) |
| RAM limitada (922 MB) | Selenium se instancia y cierra por cada scrape; scraping en serie |
| Paquetes C que necesitan compilación | Añadir `gcc libffi-dev python3-dev` al Dockerfile |
