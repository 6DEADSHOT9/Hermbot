FROM node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backend /app/backend
COPY tests /app/tests
COPY plugins /app/plugins
COPY hermbot_runtime_gate.py /app/hermbot_runtime_gate.py
COPY --from=web /web/dist /app/web-dist
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --create-home app
USER app
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
