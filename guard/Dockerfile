FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY guard.py .

# Standard library only: no third-party dependencies in the component that touches docker.sock.
RUN useradd --system --uid 10001 --no-create-home guard
USER 10001

EXPOSE 2375
HEALTHCHECK --interval=15s --timeout=3s --start-period=3s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:2375/_ping', timeout=2)"

CMD ["python", "guard.py"]
