FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY trdr ./trdr
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 trdr
USER trdr

# Un seul worker : l'état d'authentification (verrouillage, codes déjà utilisés) est en mémoire.
CMD ["uvicorn", "--factory", "trdr.api.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
