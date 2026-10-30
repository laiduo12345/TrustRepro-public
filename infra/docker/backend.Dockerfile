FROM node:24-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS codex
RUN npm install -g @openai/codex@0.154.0
RUN find /usr/local/lib/node_modules/@openai/codex/node_modules -type f -name codex -exec cp {} /usr/local/bin/codex-native \; \
    && /usr/local/bin/codex-native --version

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

RUN pip install --no-cache-dir uv==0.11.8
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --from=codex /usr/local/bin/codex-native /usr/local/bin/codex
COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
RUN uv sync --frozen --no-dev --no-editable \
    && useradd --uid 10001 --create-home trustrepro \
    && mkdir -p /app/var && chown trustrepro:trustrepro /app/var
USER trustrepro
CMD ["uvicorn", "trustrepro.main:app", "--host", "0.0.0.0", "--port", "8000"]
