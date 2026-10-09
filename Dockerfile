# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Static files are baked into the image (served by WhiteNoise).
RUN DJANGO_DEBUG=False DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /app/data /app/media /app/output \
    && chown -R app:app /app/data /app/media /app/output \
    && chmod +x docker/entrypoint.sh

USER app
VOLUME ["/app/data", "/app/media"]
EXPOSE 8000

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["web"]
