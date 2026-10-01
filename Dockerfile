FROM python:3.12-slim

WORKDIR /app

COPY stocktrend-core /stocktrend-core

ENV PYTHONUNBUFFERED=1

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "poetry>=2.0.0"

COPY stocktrend-kiwoom/pyproject.toml stocktrend-kiwoom/poetry.lock* ./

ENV PIP_DEFAULT_TIMEOUT=1200 POETRY_REQUESTS_TIMEOUT=1200
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/pypoetry \
    poetry config virtualenvs.create false \
    && poetry config requests.max-retries 3 \
    && poetry config installer.max-workers 4 \
    && poetry install --no-interaction --no-ansi --without dev --no-root

COPY stocktrend-kiwoom/src/ ./src/

EXPOSE 8028

CMD ["python", "-m", "src.main"]
