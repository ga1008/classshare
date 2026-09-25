#!/usr/bin/env bash
# DSH runtime retired (2026-09-25): the Agent now runs on the OpenAI Agents SDK
# inside the agent-worker container (classroom_app/services/agent_sdk). The
# function names stay because deployment/deploy_remote.ps1 sources and calls
# them; they now only retire the old host launcher, idempotently.
dsh_preflight() {
  echo 'Agent runtime: openai-agents in agent-worker (DSH retired); no preflight needed.'
}
dsh_activate() {
  if command -v systemctl >/dev/null 2>&1 && systemctl cat lanshare-agent-launcher.service >/dev/null 2>&1; then
    systemctl disable --now lanshare-agent-launcher.service >/dev/null 2>&1 || true
    echo 'DSH_LAUNCHER_RETIRED=lanshare-agent-launcher.service'
  fi
}
dsh_verify_and_retire() {
  echo 'Agent runtime: DSH launcher retired; nothing to verify.'
}
