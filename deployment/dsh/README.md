# DSH runtime (retired 2026-09-25)

The official DSH (deepseek-harness) container runtime and its host launcher were
retired. The Agent now runs on the **OpenAI Agents SDK** inside the existing
`agent-worker` container: see `classroom_app/services/agent_sdk/` and
`docs/agent-runtime-openai-agents-2026-09-25.md`.

Only `deploy_integration.sh` remains, because `deployment/deploy_remote.ps1`
still sources it. Its hooks now idempotently disable the old
`lanshare-agent-launcher.service` on the host; they install nothing.

Historical design, evidence and rollback notes stay in `docs/agent-dsh-*.md`.
