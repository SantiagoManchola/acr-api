# API ACR — Acueducto Comunitario Acuaricaurte

Backend REST en **FastAPI + SQLAlchemy 2.0 + MySQL (pymysql)**.
Cumple RNF-03 (auth JWT), RNF-04 (permisos por rol), RNF-06 (Pydantic),
RNF-07 (trazabilidad) y RNF-19 (exportación CSV/XLSX/PDF).

## 1. Crear la base de datos

En MySQL (línea de comandos o cliente), crea la BD `acr` en utf8mb4 y el
usuario de la app, luego ejecuta el SQL de la Fase 2:

```sql
CREATE DATABASE acr CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'acr_user'@'localhost' IDENTIFIED BY 'acr_password';
GRANT ALL PRIVILEGES ON acr.* TO 'acr_user'@'localhost';
FLUSH PRIVILEGES;
```

```bash
mysql -u acr_user -p acr < ../datamodel/migrations/acr_migrations.sql
```

> La variable `DATABASE_URL` (en `.env`) debe apuntar a esa BD:
> `mysql+pymysql://acr_user:acr_password@localhost:3306/acr`

## 2. Instalar dependencias

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edita JWT_SECRET y credenciales
```

> El hashing de contraseñas usa `bcrypt` **directo** (sin `passlib`); la versión está
> fijada en `requirements.txt` (`bcrypt==4.1.3`). `seed.py` sigue siendo necesario para
> crear roles y el usuario admin.

## 3. Sembrar roles y usuario admin

```bash
python seed.py
```

Crea los roles `admin`, `administrativo`, `operario`, `fontanero` y un
usuario admin (credenciales desde `ADMIN_USERNAME`/`ADMIN_PASSWORD` del `.env`).

## 4. Ejecutar el API

```bash
uvicorn app.main:app --reload
```

El servidor queda en `http://127.0.0.1:8000`.

## 5. Swagger / OpenAPI

Documentación interactiva automática en **`/docs`**
(`http://127.0.0.1:8000/docs`). Esquema OpenAPI en `/openapi.json`.
Para autenticar: usa `POST /auth/login` (username/password) y pega el
`access_token` con el botón "Authorize" (prefijo `Bearer `).

## Estructura

```
api/
  app/
    main.py          # FastAPI app + CORS + routers
    config.py        # settings (pydantic-settings, desde .env)
    db.py            # engine / SessionLocal / Base
    models.py        # 16 tablas SQLAlchemy (MySQL)
    schemas.py       # Pydantic v2
    security.py      # JWT, bcrypt, get_current_user, require_role
    routers/         # auth, usuarios, inventario, micromedidores, planta, reportes
    services/        # lógica de negocio (consumo, fuera de rango, export, auditoría)
  seed.py            # roles base + admin
  requirements.txt
  .env.example
```

## Resumen de endpoints por módulo

- **Auth:** `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`.
- **Usuarios/Roles:** `GET/POST /usuarios/roles`, `GET/POST /usuarios`,
  `GET/PATCH/DELETE /usuarios/{id}` (DELETE = soft delete).
- **Inventario:** `GET/POST /inventario`, `GET/PATCH/DELETE /inventario/{id}`,
  `POST /inventario/{id}/entrada`, `POST /inventario/{id}/salida`,
  `GET /inventario/movimientos`, `GET /inventario/alertas`.
- **Micromedidores:** `GET/POST /suscriptores`, `GET/POST /micromedidores`,
  `POST /lecturas` (calcula consumo/promedio), `GET /lecturas`,
  `GET /consumo/sector/{sector}`.
- **Planta:** `GET/POST /planta/parametros`, `POST /planta/mediciones`
  (marca `fuera_rango`), `GET /planta/mediciones/fuera-rango`,
  `POST /planta/dosificaciones` (los químicos son insumos en `/inventario`),
  `POST /planta/actividades`, `POST /planta/horas-servicio`.
- **Reportes:** `GET /reportes/inventario`, `/reportes/consumo`,
  `/reportes/planta` con `?formato=csv|xlsx|pdf` (sin formato = JSON).
- **Evidencias:** `POST /evidencias/presign` (firma subida PUT directa a R2),
  `POST /evidencias/confirmar` (verifica tamaño; borra si excede 8 MB).

## Evidencias fotográficas (Cloudflare R2)

Mediciones de parámetros, lecturas de micromedidores y actividades aceptan
`foto_url` **opcional**. El navegador comprime la foto (WebP, máx 1600px) y la
sube **directo a R2 con URL firmada PUT**; el API solo guarda la URL pública.

> R2 **NO soporta subidas POST** (presigned POST / formularios HTML): responde
> `501 Not Implemented`. Por eso el flujo usa PUT firmado, que sí está
> soportado. Si ves un 501 contra `*.r2.cloudflarestorage.com` con método
> POST, es esto: actualiza el CMS (`cms/src/api/evidencias.js` ya usa PUT).

### 1. Migración de BD

```bash
mysql -u acr_user -p acr < ../datamodel/migrations/acr_migrations_006_foto_url.sql
```

### 2. Crear el bucket en Cloudflare

1. Dashboard Cloudflare → **R2** → *Create bucket* (ej. `acr-evidencias`).
2. En el bucket → **Settings** → *Public access* → **Allow Access**
   (o conecta un dominio propio en *Custom Domains*). Anota la URL pública,
   ej. `https://pub-xxx.r2.dev`.
3. **CORS** del bucket (obligatorio para subir desde la plataforma web).
   Debe permitir **PUT** (no POST) y el header `Content-Type` explícito
   (R2 rechaza el comodín `"*"` en `AllowedHeaders`):

```json
[
  {
    "AllowedOrigins": ["https://tu-plataforma.com", "http://localhost:5173"],
    "AllowedMethods": ["GET", "PUT"],
    "AllowedHeaders": ["Content-Type"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

4. **Manage R2 API Tokens** → crea un token con permiso *Object Read & Write*
   sobre ese bucket. Anota `Access Key ID` y `Secret Access Key`.

### 3. Variables en `api/.env`

```bash
R2_ACCOUNT_ID=tu_account_id
R2_ACCESS_KEY_ID=tu_access_key
R2_SECRET_ACCESS_KEY=tu_secret
R2_BUCKET=acr-evidencias
R2_PUBLIC_BASE_URL=https://pub-xxx.r2.dev
```

Sin estas variables, `POST /evidencias/presign` responde `501` y los registros
se guardan sin foto (la foto es opcional en los 3 puntos).
