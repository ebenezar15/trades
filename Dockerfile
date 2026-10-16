# Optional: run IndexEdge on a Linux VPS. The Windows native install is the primary path.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
WORKDIR /app
COPY pyproject.toml ./
COPY indexedge ./indexedge
COPY config ./config
RUN pip install --no-cache-dir -e .
COPY --from=web /web/dist ./frontend/dist
EXPOSE 8000
CMD ["python", "-m", "indexedge", "run", "--no-browser"]
