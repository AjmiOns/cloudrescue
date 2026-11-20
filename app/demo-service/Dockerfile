FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY demo_app.py .

# Unprivileged user; the compose file also drops all capabilities and mounts / read-only.
RUN useradd --system --uid 10001 --no-create-home app
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "demo_app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
