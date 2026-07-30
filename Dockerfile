FROM python:3.12.10-slim

WORKDIR /app

RUN useradd --create-home --shell /bin/bash appuser

# Install pinned deps first for a cacheable layer.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /data && chown appuser:appuser /data

USER appuser

EXPOSE 8000

CMD ["python", "-m", "clickup_mcp"]
