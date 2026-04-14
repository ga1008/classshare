FROM lanshare_base

ARG APP_VERSION=dev
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

LABEL org.opencontainers.image.title="LanShare" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements.lock.txt requirements-docker.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -i "${PIP_INDEX_URL}"

COPY deployment/docker/entrypoint.sh /usr/local/bin/lanshare-entrypoint
RUN chmod +x /usr/local/bin/lanshare-entrypoint

COPY . .

RUN mkdir -p \
    /app/attendance \
    /app/chat_logs \
    /app/data \
    /app/homework_submissions \
    /app/logs \
    /app/rosters \
    /app/shared_files \
    /app/storage/chunked_uploads \
    /app/storage/global_files

EXPOSE 8000 8001

ENTRYPOINT ["lanshare-entrypoint"]
CMD ["main"]
