FROM node:24-slim AS web-build
WORKDIR /workspace
RUN corepack enable
COPY . .
RUN pnpm install --frozen-lockfile
RUN PORT=5173 BASE_PATH=/ pnpm --filter @workspace/support-intelligence run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    SUPPORT_DB_PATH=/data/support.db \
    LINEAGE_LOG_PATH=/data/lineage_events.jsonl \
    WEB_STATIC_DIR=/app/web
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt
COPY main.py ./
COPY support_backend ./support_backend
COPY --from=web-build /workspace/artifacts/support-intelligence/dist/public ./web
RUN mkdir -p /data && useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app /data
USER appuser
EXPOSE 8000
CMD ["python", "main.py"]
