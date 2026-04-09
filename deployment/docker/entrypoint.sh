#!/bin/sh
set -eu

mkdir -p \
  /app/attendance \
  /app/chat_logs \
  /app/data \
  /app/homework_submissions \
  /app/logs \
  /app/rosters \
  /app/shared_files \
  /app/storage/chunked_uploads \
  /app/storage/global_files

service="${1:-main}"

case "$service" in
  main)
    shift || true
    exec python -u main.py "$@"
    ;;
  ai)
    shift || true
    exec python -u ai_assistant.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
