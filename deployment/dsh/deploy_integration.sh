#!/usr/bin/env bash
# Tracked core integration. deployment/deploy_remote.ps1 sources these functions.
# Called only after source extraction; activation follows quiesced PG migration.
dsh_preflight() {
  local root="${1:?canonical project root required}"
  local quiesced="${2:-False}"
  if [ "$quiesced" != True ]; then
    echo 'DSH deployment requires the native PostgreSQL gate and quiesced schema migration.' >&2
    return 2
  fi
  python3 "$root/deployment/dsh/install_launcher.py" --root "$root" --mode preflight
}
dsh_activate() {
  local root="${1:?canonical project root required}"
  python3 "$root/deployment/dsh/install_launcher.py" --root "$root" --mode activate
}
dsh_verify_and_retire() {
  local root="${1:?canonical project root required}"
  # Both launcher evidence and loopback application health are mandatory,
  # including releases that skip the deployer's optional broad health checks.
  python3 "$root/deployment/dsh/install_launcher.py" --root "$root" --mode retire-legacy
}
