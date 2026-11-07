FROM python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backend /app/backend
COPY tests /app/tests
COPY plugins /app/plugins
COPY hermbot_runtime_gate.py /app/hermbot_runtime_gate.py
COPY model_broker /app/model_broker
COPY pyproject.toml /app/pyproject.toml
CMD ["pytest", "-q"]
