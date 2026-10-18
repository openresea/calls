FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app

RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# Select which service to run:
#   app.phonebanking.main:app  - caller interface      (port 8001)
#   app.admin.main:app         - admin management UI   (port 8002)
ENV APP_MODULE=app.phonebanking.main:app \
    PORT=8001

CMD ["sh", "-c", "exec uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
