FROM node:22-alpine AS viewer-build
WORKDIR /viewer
COPY viewer/package*.json ./
RUN npm ci
COPY viewer/ ./
RUN npm run build

FROM node:22-alpine AS embed-build
WORKDIR /embed
COPY embed/package*.json ./
RUN npm ci
COPY embed/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --upgrade pip && pip install -e .

COPY demo ./demo
COPY scripts ./scripts

COPY --from=viewer-build /viewer/dist ./static/viewer
COPY --from=embed-build /embed/dist ./static/embed

ENV DATA_DIR=/app/data
RUN mkdir -p /app/data/pdfs

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}"]
