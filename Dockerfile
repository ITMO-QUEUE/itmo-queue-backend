FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY alembic ./alembic
RUN useradd --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "itmo_queue.main:app", "--host", "0.0.0.0", "--port", "8000"]
