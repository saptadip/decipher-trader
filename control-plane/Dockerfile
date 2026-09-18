FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.4.20
COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
RUN uv pip install --system -e "."

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY --from=build /app /app
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn control_plane.main:app --host 0.0.0.0 --port 8000"]
