FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Level-2 escalation (EC2 reboot) needs boto3; it is only installed when explicitly requested.
ARG WITH_AWS=false

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && if [ "$WITH_AWS" = "true" ]; then pip install boto3==1.35.36; fi

COPY *.py config.yaml ./

# /data holds the SQLite incident log; the compose file mounts a named volume there.
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir /data && chown 10001:10001 /data
USER 10001

EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8001/metrics', timeout=2)"

CMD ["python", "main.py"]
