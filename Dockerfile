ARG LANSHARE_FRONTEND_BASE=lanshare-frontend-deps:current
ARG LANSHARE_RUNTIME_BASE=lanshare-runtime-deps:current
FROM ${LANSHARE_FRONTEND_BASE} AS frontend-builder

WORKDIR /app

COPY package.json package-lock.json ./
COPY deployment/docker ./deployment/docker

# Only source compilation happens here, even after Docker's build cache is pruned.
COPY postcss.config.js tailwind.config.js tsconfig.json vite.config.ts ./
COPY frontend ./frontend
COPY classroom_app ./classroom_app
COPY static/css/ui-system.src.css ./static/css/ui-system.src.css
COPY static/js ./static/js
COPY templates ./templates

RUN sh /usr/local/bin/lanshare-verify-dependencies frontend && npm run build

FROM ${LANSHARE_RUNTIME_BASE}

ARG APP_VERSION=dev

LABEL org.opencontainers.image.title="LanShare" \
      org.opencontainers.image.version="${APP_VERSION}"

WORKDIR /app

COPY deployment/docker/entrypoint.sh /usr/local/bin/lanshare-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/lanshare-entrypoint \
    && chmod +x /usr/local/bin/lanshare-entrypoint

COPY . .
RUN sh /usr/local/bin/lanshare-verify-dependencies runtime
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
