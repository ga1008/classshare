"""LanShare Agent runtime on the OpenAI Agents SDK (``openai-agents``).

The Agent is the user's virtual assistant: it runs in the ``agent-worker``
container, talks to DeepSeek V4.1 Flash (multimodal, thinking mode) through
the OpenAI-compatible Chat Completions API, and operates the platform only
through ``/api/agent-bridge`` with a per-task credential bound to the user's
live login session. See ``docs/agent-runtime-openai-agents-2026-09-25.md``.

Modules:
- ``model``    – credential/endpoint resolution and model settings
- ``bridge``   – async JSON-RPC client for the platform MCP bridge
- ``recorder`` – structured, user-facing task events (thinking / decision /
                 tool / operation / question / result)
- ``tools``    – the function tools exposed to the model
- ``prompts``  – system instructions and the first user message
- ``state``    – park / resume / finalize with attempt fencing
- ``runner``   – one execution segment of a task
"""
