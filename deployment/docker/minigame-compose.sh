#!/usr/bin/env bash
# Source from the existing release script after it chooses the base compose files.
configure_minigames_overlay() {
  local phase="${1:-after_extract}"
  minigames_enabled=False
  if [ ! -f "$remote_path/minigames.env" ]; then return 0; fi
  if [ "$db_engine" != "postgres" ]; then
    echo "The minigames overlay requires the PostgreSQL deployment." >&2
    return 2
  fi
  if [ ! -f "$remote_path/docker-compose.minigames.yml" ]; then
    if [ "$phase" = "before_extract" ]; then return 0; fi
    echo "Missing minigames compose overlay in the release." >&2
    return 2
  fi
  compose_cmd=(docker compose)
  if [ -f "$remote_path/.env" ]; then compose_cmd+=(--env-file .env); fi
  compose_cmd+=(--env-file minigames.env -f docker-compose.yml -f docker-compose.postgres.yml -f docker-compose.minigames.yml)
  compose_files_description="$compose_files_description + docker-compose.minigames.yml"
  minigames_enabled=True
}

minigames_postgres_id() {
  local id
  id="$("${compose_cmd[@]}" ps -q postgres)" || return 1
  if [ -z "$id" ] || [ "$(docker inspect --format '{{.State.Health.Status}}' "$id")" != healthy ]; then
    echo "The existing PostgreSQL container must be running and healthy." >&2
    return 1
  fi
  printf '%s\n' "$id"
}

minigames_check_postgres() {
  local current
  current="$(minigames_postgres_id)" || return 1
  if [ "$current" != "$minigames_previous_postgres" ]; then
    echo "PostgreSQL container identity changed during the application release." >&2
    return 1
  fi
  echo "POSTGRES_PRESERVED=$current"
}

minigames_start_classroom() {
  # PostgreSQL has already served the migration. The shared docker.env contains
  # the changing classroom release ID and must not trigger database replacement.
  local update=(up -d --no-deps --no-build --pull never --wait --wait-timeout 240)
  minigames_check_postgres || return 1
  "${compose_cmd[@]}" "${update[@]}" ai || return 1
  "${compose_cmd[@]}" "${update[@]}" app || return 1
  "${compose_cmd[@]}" "${update[@]}" mailer blog-crawler scheduler agent-worker nginx || return 1
}

minigames_verify_running() {
  "${compose_cmd[@]}" exec -T nginx nginx -t || return 1
  local service kind api_id content_id
  api_id="$("${compose_cmd[@]}" ps -q game-api)" || return 1
  content_id="$("${compose_cmd[@]}" ps -q game-content)" || return 1
  docker inspect --format '{{json .Mounts}}' "$api_id" "$content_id" |
    python3 "$remote_path/deployment/docker/minigame_health.py" mounts --app-root "$remote_path" || return 1
  for kind in api content; do
    service="game-$kind"
    "${compose_cmd[@]}" exec -T "$service" python /app/deployment/docker/minigame_health.py "$kind" || return 1
    "${compose_cmd[@]}" exec -T "$service" python /app/deployment/docker/minigame_health.py "$kind" --via-nginx || return 1
  done
}
