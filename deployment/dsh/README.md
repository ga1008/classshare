# Fixed DSH runner and controlled deployment

The image has been built and tested on the target Linux host in an isolated
directory; production services have not been switched by this work. The exact
tested image ID and profile digest are in `release.json`. Pin: official `@deepseek-ai/dsh`
`0.1.5-rc.1`; Node `24.13.0` image manifest is pinned by digest in Dockerfile.
`package-lock.json` pins npm dependencies and integrity values. Build from the
repository root using `docker build -f deployment/dsh/Dockerfile .`.
The image entrypoint accepts `--evidence` as its only probe mode and returns
`{"dsh_package_version":"0.1.5-rc.1","profile_sha256":"..."}` without task
environment, gateway access or model calls. The launcher must compare it with
the expected dependency version and normalized profile digest before launch.
The host launcher precreates the empty `node_modules` and
`.dsh-module-fallback/node_modules` directories inside the profile: the pinned
official loader invokes `mkdir` for both even when all plugins are bundled.
It also rewrites `profile/cordis.yml` to a fixed empty root on every boot. The
launcher mounts only that generated file writable from the attempt's private
home; the manifest and patch remain on the read-only directory mount. The
entrypoint verifies both exact mounts. This narrow exception is required by
the unmodified official CLI; a writable profile directory is not accepted.

The launcher must supply a separate non-root container per task with `--network none`, a read-only root
filesystem, CPU/memory/PID quotas, a bounded `/tmp`, an isolated writable
`/workspace` and `/var/lib/dsh`, and **the exact `profile/` directory mounted
read-only at `/var/lib/dsh/profiles/lanshare`**. Only the latter is immutable;
session, attachment and credential bookkeeping use the isolated DSH home. Mount
the task gateway socket at `/run/lanshare-agent/gateway.sock`; the Node supervisor
starts a `127.0.0.1:8787` relay to this socket before starting ACP. The relay only
forwards the two model paths and `/api/agent-bridge/` paths, preserving streaming
backpressure and cancellation; the gateway must still enforce task credentials.
The Linux relay, network isolation, filesystem, shell, cancellation and container
removal have passed the actual target-host fixture in
`docs/agent-dsh-linux-poc-2026-09-10.json`. Shell uses the official Landlock runner;
this host reports partial enforcement on its older Landlock ABI. Docker's
network-none/read-only/capability/UID limits were separately inspected. The
entrypoint checks the read-only mount and the image's profile digest, refuses
caller commands/profiles, and rebuilds the environment from a narrow allowlist.
Do not mount the host source tree, Docker socket, database credentials or shared
user directories. Read-only mounts and network rules belong to the launcher;
proxy environment variables are not inherited by the runtime.
Home-level `cordis.patch.yml`, home `.env` and workspace `.env` are rejected at
startup. The pinned CLI reads those after the environment/profile has been set;
accepting them would invalidate the declared immutable configuration. Default
home/project skill discovery is also off; only the image-owned skill directory
is enabled. Docker's separate stdout/stderr log driver is disabled; the platform
owns admitted event persistence and the isolated DSH home owns session state.

Required launch variables:

| Variable | Meaning |
|---|---|
| `DSH_TASK_ID`, `DSH_ACTOR_ID`, `DSH_ATTEMPT_ID`, `DSH_FENCING_TOKEN` | Server-derived identity; actor is `role:id` |
| `DSH_GATEWAY_BASE_URL` | Exactly `http://127.0.0.1:8787/api/agent-model`; DSH appends `/chat/completions` or `/messages` |
| `DSH_GATEWAY_MODEL` | Active configured main model id, explicitly selected |
| `DSH_GATEWAY_TOKEN` | Short-lived task model-gateway credential, never a real provider API key |
| `DSH_BROKER_MCP_URL`, `DSH_BROKER_TOKEN` | MCP under `http://127.0.0.1:8787/api/agent-bridge/` and a distinct task credential |
| `DSH_SEARCH_MODEL` | Optional explicitly configured search model; absence disables search |

No raw DeepSeek key, platform session cookie or complete `docker.env` is accepted
by the runtime environment. The launcher and gateway must validate task tokens,
their scopes, active attempts, fencing tokens and revocation. The profile caps
chat output at 16,384 tokens; gateway task budgets and concurrency remain
authoritative. Search is native Anthropic-compatible Messages traffic and needs
its own upstream configuration. The profile declares text input only: Files API
and image acceptance are not enabled or verified here.

Platform MCP is required in `session/new.mcpServers` (and resume), using
`{"type":"http","name":"lanshare","url":"http://127.0.0.1:8787/api/agent-bridge/mcp","headers":[{"name":"Authorization","value":"Bearer <task-tools-token>"}]}`.
The official ACP adapter awaits discovery and rolls back an unpublished session
on failure. There is no redundant global MCP connection. An `initialize` reply
alone does not establish readiness; the worker must finish session creation and
verify its bound platform identity before admitting user work. The official
`ask_user_question` tool is activated with the image-owned LanShare answerer.
ACP startup requires the answerer service; a missing task credential prevents
readiness. It posts a bounded question batch, polls the same platform UUID and
returns the exact answer to the original waiting tool. Cancellation and expiry
close acknowledged questions; the platform task finalizer also closes pending
questions, including a create whose response was lost before cancellation.
An answer is not permission to perform a business mutation.

Subagent/fork/workflow/Ralph remain disabled. The pinned subagent tool can cap
depth, but its spawn backend has no child-concurrency configuration and fresh
child scopes do not import parent tool restrictions. Shared model/HTTP budgets
do not cap simultaneous child shell/file operations. Enabling it requires a
child admission semaphore, scoped MCP composition and disposal/cancel tests.

The image includes signed Debian packages for DOCX/XLSX/PDF/Pillow/XlsxWriter.
Bookworm has no `python3-pptx` package: `artifact-requirements.txt` fixes
python-pptx 1.0.2 and typing-extensions 4.12.2 wheels by SHA256 in an immutable
venv that uses the Debian libraries. `python3` in DSH shell tools selects this
venv. Linux fixtures generated and reopened DOCX, XLSX, PPTX and PNG, and checked
PDF creation/header. No Office renderer or visual-layout fidelity is claimed.

Local protocol reproduction, using an isolated deterministic model fixture:

```powershell
npm.cmd install --prefix .codex-temp/dsh-poc --no-audit --no-fund --save-exact '@deepseek-ai/dsh@0.1.5-rc.1'
python tools/dsh_acp_poc.py --report docs/agent-dsh-poc-2026-09-10.json
python -m unittest discover -s tests -p test_agent_acp_client.py -v
python -m unittest discover -s tests -p test_agent_dsh_provider.py -v
```

The ACP and Linux PoCs use actual official DSH and its ACP/MCP bridge, but model
responses come from a local HTTP fixture. The separate explicitly authorized
real-service probe in `docs/agent-dsh-real-service-probe-2026-09-10.json` made
three calls using the active system key in the trusted existing app process:
ordinary and high-reasoning V4 Pro both completed; V4 Flash native search returned
one native result block with ten nonempty-URL sources and one charged search.
The small search reply hit its token limit; the API reported 589 output tokens
despite the requested 512. Charge actual returned usage, not the requested cap.
This service test proves neither actor authorization nor end-to-end production
task readiness. Files/image model input remain unsupported. Native
`dsh-web-fetch-http` is disabled because direct public networking is intentionally
unavailable. The platform Broker supplies `public_fetch` through the same local
socket route and enforces public-address/redirect/size limits. Its platform tests
are separate from this image fixture; no direct internet access is granted.

## Deployment phases

`install_launcher.py` provides preflight, stage, activate, verify and
retire-legacy phases. It accepts only canonical `/lanshare` on the host, verifies
the immutable image/profile evidence and checks exclusive loopback port 18000
ownership. Stage writes the fixed systemd service and launcher, normalizes the
finite profile paths and saves private backups of previous service/code/env.
Stage does not start services. Activate is the explicit cutover: it removes
legacy TUI environment controls, preserves other platform keys, sets DSH=true
and host/worker concurrency=1, configures native Flash search, then restarts the
launcher. No real model key enters that service or a runner.

The tracked `deploy_integration.sh` is sourced by the local operator's
gitignored `deployment/deploy_remote.ps1`: preflight precedes application
build; activate follows the successful quiesced PostgreSQL migration; compose
startup follows activation. Mandatory launcher evidence and loopback app health
checks precede removal of the exact legacy Compose runtime container. The old
runtime data/image remain for deliberate rollback planning; no automatic
database rollback or broad orphan removal is performed. Existing native PG
backup and source-digest gates still apply. An activation failure leaves writers
stopped/partially restarted and requires inspection before resuming them.
The local operator script remains ignored because it belongs to the existing
machine-specific deployment setup. Its ordering regression runs when that file
is present; a clean source checkout explicitly skips only that local check.
The tracked installer, phase functions and their behavior checks remain part of
every checkout. Before a release, the operator-script check and native database
gate must run on the actual deployment machine.

The worker mounts only `/run/lanshare-agent/control` read-only; it cannot see
the adjacent gateway socket, task env files or singleton lock. The host service
has strict filesystem write paths, a 16-connection limit per Unix listener and
bounded process resources. The real service-process fixture proved singleton
exclusion, permissions, separate sockets, rejection of a 17th connection and
clean SIGTERM exit. See `docs/agent-dsh-launcher-service-poc-2026-09-10.json`.

Maintenance-only paid service check (requires explicit execution authorization):

```sh
python tools/dsh_upstream_smoke.py                       # zero requests; show contract
python tools/dsh_upstream_smoke.py --execute-paid-probe --expected-key-id 1
```

The opt-in check makes exactly three bounded synthetic-prompt requests, reads
only the explicitly selected active system key, performs no user impersonation
or platform action, and emits no credentials or upstream content. Do not use
this as a substitute for the post-migration real task/gateway acceptance.

The separate target-host task acceptance in
`docs/agent-dsh-isolated-e2e-2026-09-10.md` used an independent application,
schema-only synthetic database and the frozen official DSH image. Teacher,
student and administrator tasks completed through real MCP/model traffic:
18 business checks and 8 lifecycle/identity checks passed, with 17 completed
model requests. The isolated services were then stopped. This verifies an
intermediate source snapshot; it is not a production cutover or final-source
acceptance. A separate intermediate production-dump rehearsal ran full startup
migration twice across 232 original tables and 474,456 original rows, with no
unexpected original-field changes and an idempotent second pass. Final release
still requires the exact frozen-source native PostgreSQL gate and real tasks
against that source before production acceptance.
