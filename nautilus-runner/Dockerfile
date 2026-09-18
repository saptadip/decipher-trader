FROM python:3.12-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.4.20
COPY pyproject.toml ./
COPY src ./src
COPY strategies ./strategies
RUN uv pip install --system -e "."

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY --from=build /app /app
CMD ["python", "-m", "nautilus_runner.main"]
