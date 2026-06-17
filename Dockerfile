FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /app

# Install node deps in their own layer so `npm ci` (the slow part, minutes) is
# cached across code-only deploys and only re-runs when the lockfile changes.
COPY package.json package-lock.json ./
RUN npm ci

# Copy sources after the install layer so editing app code only re-runs the
# (much faster) `npm run build`, not the dependency install.
COPY postcss.config.js tailwind.config.js tsconfig.json vite.config.ts ./
COPY frontend ./frontend
COPY classroom_app ./classroom_app
COPY static/css/ui-system.src.css ./static/css/ui-system.src.css
COPY static/js ./static/js
COPY templates ./templates

RUN npm run build

FROM lanshare_base

ARG APP_VERSION=dev
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

LABEL org.opencontainers.image.title="LanShare" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    APP_TIMEZONE=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements.lock.txt requirements-docker.txt ./
RUN python -m pip uninstall -y fitz >/dev/null 2>&1 || true \
    && python -m pip install --no-cache-dir -r requirements.txt -i "${PIP_INDEX_URL}"

COPY deployment/docker/entrypoint.sh /usr/local/bin/lanshare-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/lanshare-entrypoint \
    && chmod +x /usr/local/bin/lanshare-entrypoint

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git \
        tzdata \
        fonts-arphic-uming \
        antiword \
        libarchive-tools \
        libreoffice-calc \
        libreoffice-impress \
        libreoffice-writer \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" > /etc/timezone \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY . .
COPY --from=frontend-builder /app/static/css/tailwind-app.css /app/static/css/tailwind-app.css
COPY --from=frontend-builder /app/static/dist /app/static/dist

RUN mkdir -p \
    /app/data \
    /app/data/db \
    /app/data/files/legacy_shared \
    /app/data/files/submissions \
    /app/data/files/textbook_attachments \
    /app/data/imports/attendance \
    /app/data/imports/rosters \
    /app/data/logs/chat_logs \
    /app/data/media/blobs/sha256 \
    /app/data/runtime \
    /app/data/tmp/chunked_uploads \
    /app/attendance \
    /app/chat_logs \
    /app/homework_submissions \
    /app/logs \
    /app/rosters \
    /app/shared_files \
    /app/storage/chunked_uploads \
    /app/storage/global_files \
    /app/storage/textbook_attachments

EXPOSE 8000 8001

ENTRYPOINT ["/usr/local/bin/lanshare-entrypoint"]
CMD ["main"]
