FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend

# Usuário sem privilégios para processos que não precisam escrever nos
# volumes montados (ex.: webhook-worker via docker-compose `user:` — ver
# LEI 01/07 de isolamento). backend-gateway continua root por padrão
# porque grava em /data (volume `backend-data`).
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser

EXPOSE 8080

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
