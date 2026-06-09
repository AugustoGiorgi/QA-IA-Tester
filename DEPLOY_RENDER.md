# Deploy en Render

## Servicio

Crear un **Web Service** desde el repo del proyecto usando el archivo `render.yaml`.

Render debe ejecutar:

- Build command: `cd backend && pip install --upgrade pip && pip install --no-cache-dir --force-reinstall -r requirements.txt`
- Start command: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/health`

## Variables obligatorias

Configurar en Render:

- `MONGO_URI`: string de conexion a MongoDB Atlas.
- `MONGO_DB`: `Life_Projects` o la base que quieras usar.
- `OPENAI_API_KEY`: clave de OpenAI para las funciones IA.
- `APP_BASE_URL`: URL final de Render, por ejemplo `https://qa-doc-analyzer.onrender.com`.
- `BOOTSTRAP_ADMIN_PASSWORD`: password inicial del usuario lider si la coleccion `Users` esta vacia.
- `BOOTSTRAP_ADMIN_EMAIL`: email del usuario lider inicial.

## Variables opcionales para mails

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_TLS`

## Notas

- Render ya incluye `ffmpeg` en el runtime nativo, por eso el modo video de Playwright puede extraer frames sin Docker.
- El filesystem de Render es efimero si no se agrega un disco persistente. MongoDB conserva usuarios, tareas y registros, pero archivos guardados en `backend/data` pueden perderse en redeploys o reinicios.
- Si el equipo necesita conservar descargas generadas, adjuntos o videos por mucho tiempo, agregar un disco persistente en Render o mover esos archivos a almacenamiento externo.
