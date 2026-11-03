FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    PLATFORM_DATA_DIR=/data

WORKDIR /app

COPY requirements-platform.txt ./
RUN pip install --no-cache-dir -r requirements-platform.txt

COPY app ./app

RUN useradd --create-home --uid 1000 platform \
    && mkdir -p /data \
    && chown -R platform:platform /app /data
USER platform

EXPOSE 8800

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8800"]
