# Investigation-only service, no build tooling needed at runtime beyond
# what pip install pulls in — same slim-base pattern the rest of this
# stack doesn't use custom Dockerfiles for (this is the first Python
# service in the stack), kept minimal on purpose.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY prompts ./prompts

# Incident state (app/state/store.py) lives here — not a named volume by
# default, matching this stack's own teaching-stack convention (tempo/
# loki/prometheus data doesn't survive a recreate either); add a volume
# mount in docker-compose.yml if incident history should persist.
RUN mkdir -p /data

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
