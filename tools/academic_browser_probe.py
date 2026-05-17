from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

try:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Error as PlaywrightError,
        Page,
        Request,
        Response,
        sync_playwright,
    )
except ImportError as exc:  # pragma: no cover - this is an operator setup error.
    raise SystemExit(
        "Playwright is not installed. Run:\n"
        "  venv\\Scripts\\python.exe -m pip install -r tools\\academic_browser_probe_requirements.txt\n"
        "Then install a browser or use --browser-executable with Chrome/Edge."
    ) from exc


DEFAULT_TARGET_URL = (
    "https://jwxt.gxufl.com/kbcx/jskbcx_cxJskbcxIndex.html?"
    "doType=details&gnmkdm=N2150&layout=default"
)
DEFAULT_LOGIN_URL = "https://jwxt.gxufl.com/xtgl/login_slogin.html"
DEFAULT_BASE_URL = "https://jwxt.gxufl.com"

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
}
SENSITIVE_FIELD_PATTERNS = (
    re.compile(r"pass(word)?", re.I),
    re.compile(r"pwd", re.I),
    re.compile(r"mm$", re.I),
    re.compile(r"token", re.I),
    re.compile(r"csrf", re.I),
    re.compile(r"session", re.I),
    re.compile(r"cookie", re.I),
    re.compile(r"yhm$", re.I),
    re.compile(r"user(name)?", re.I),
)
TEXTUAL_CONTENT_MARKERS = (
    "application/json",
    "text/",
    "application/javascript",
    "application/x-javascript",
    "application/xml",
    "application/xhtml+xml",
    "application/x-www-form-urlencoded",
)
SKIP_BODY_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_key_value(key: str, value: Any) -> Any:
    if any(pattern.search(str(key or "")) for pattern in SENSITIVE_FIELD_PATTERNS):
        if value in (None, ""):
            return value
        return "[REDACTED]"
    return value


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in (headers or {}).items():
        normalized = key.lower()
        if normalized in SENSITIVE_HEADER_KEYS:
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value
    return sanitized


def parse_post_data(post_data: str | None, content_type: str = "") -> dict[str, Any]:
    raw = post_data or ""
    if not raw:
        return {"kind": "empty", "fields": {}, "raw_preview": ""}

    lowered = content_type.lower()
    if "application/x-www-form-urlencoded" in lowered or ("=" in raw and "&" in raw):
        pairs = parse_qsl(raw, keep_blank_values=True)
        fields: dict[str, Any] = {}
        for key, value in pairs:
            safe_value = sanitize_key_value(key, value)
            if key in fields:
                existing = fields[key]
                if not isinstance(existing, list):
                    fields[key] = [existing]
                fields[key].append(safe_value)
            else:
                fields[key] = safe_value
        return {"kind": "form", "fields": fields, "raw_preview": redact_text(raw[:2000])}

    if "json" in lowered:
        try:
            parsed = json.loads(raw)
            return {"kind": "json", "fields": redact_json(parsed), "raw_preview": redact_text(raw[:2000])}
        except json.JSONDecodeError:
            pass

    return {"kind": "raw", "fields": {}, "raw_preview": redact_text(raw[:2000])}


def redact_text(text: str) -> str:
    redacted = str(text or "")
    redacted = re.sub(r"(?i)(mm|password|pwd|csrftoken|token|yhm|username)=([^&\s]+)", r"\1=[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(JSESSIONID|route)=([^;,\s]+)", r"\1=[REDACTED]", redacted)
    return redacted


def redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): sanitize_key_value(str(k), redact_json(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    return value


def is_textual_response(content_type: str, resource_type: str) -> bool:
    if resource_type in SKIP_BODY_RESOURCE_TYPES:
        return False
    lowered = (content_type or "").lower()
    return any(marker in lowered for marker in TEXTUAL_CONTENT_MARKERS) or not lowered


def decode_body(raw_body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([^;\s]+)", content_type or "", re.I)
    encodings = []
    if charset_match:
        encodings.append(charset_match.group(1).strip("\"'"))
    encodings.extend(["utf-8", "gb18030", "gbk", "latin-1"])
    for encoding in encodings:
        try:
            return raw_body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw_body.decode("utf-8", errors="replace")


def compact_body_summary(text: str, content_type: str) -> dict[str, Any]:
    content = text or ""
    lowered = (content_type or "").lower()
    summary: dict[str, Any] = {
        "char_count": len(content),
        "preview": redact_text(re.sub(r"\s+", " ", content[:1200])).strip(),
    }
    if "json" in lowered:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return summary
        summary["json"] = summarize_json(parsed)
    return summary


def summarize_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        keys = list(value.keys())
        array_lengths = {
            str(key): len(item)
            for key, item in value.items()
            if isinstance(item, list)
        }
        dict_keys = {
            str(key): list(item.keys())[:40]
            for key, item in value.items()
            if isinstance(item, dict)
        }
        samples = {}
        for key, item in value.items():
            if isinstance(item, list) and item:
                first = item[0]
                if isinstance(first, dict):
                    samples[str(key)] = {"first_item_keys": list(first.keys())[:80]}
                else:
                    samples[str(key)] = {"first_item_type": type(first).__name__}
        return {
            "type": "object",
            "keys": keys[:80],
            "array_lengths": array_lengths,
            "dict_keys": dict_keys,
            "samples": samples,
        }
    if isinstance(value, list):
        first = value[0] if value else None
        first_keys = list(first.keys())[:80] if isinstance(first, dict) else []
        return {
            "type": "array",
            "length": len(value),
            "first_item_type": type(first).__name__ if first is not None else "",
            "first_item_keys": first_keys,
        }
    return {"type": type(value).__name__}


def host_matches(url: str, host_filter: str) -> bool:
    if not host_filter:
        return True
    return urlparse(url).netloc.lower().endswith(host_filter.lower())


def default_browser_executable() -> str:
    candidates = [
        os.environ.get("BROWSER_EXECUTABLE", ""),
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        str(Path.home() / r"AppData\Local\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


@dataclass
class CaptureStore:
    output_dir: Path
    host_filter: str
    max_body_chars: int
    records: list[dict[str, Any]] = field(default_factory=list)
    request_ids: dict[int, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    sequence: int = 0

    def request_record(self, request: Request) -> dict[str, Any]:
        object_id = id(request)
        if object_id in self.request_ids:
            return self.records[self.request_ids[object_id]]

        self.sequence += 1
        headers = sanitize_headers(request.headers)
        content_type = request.headers.get("content-type", "")
        post_data = request.post_data if request.method.upper() != "GET" else ""
        parsed_url = urlparse(request.url)
        record = {
            "id": self.sequence,
            "elapsed_ms": int((time.monotonic() - self.started_at) * 1000),
            "method": request.method,
            "url": request.url,
            "scheme": parsed_url.scheme,
            "host": parsed_url.netloc,
            "path": parsed_url.path,
            "query": parsed_url.query,
            "resource_type": request.resource_type,
            "request_headers": headers,
            "post_data": parse_post_data(post_data, content_type),
            "status": None,
            "response_headers": {},
            "response_content_type": "",
            "response_summary": {},
            "response_body_file": "",
            "failure": "",
        }
        self.records.append(record)
        self.request_ids[object_id] = len(self.records) - 1
        return record

    def on_request(self, request: Request) -> None:
        self.request_record(request)

    def on_request_failed(self, request: Request) -> None:
        record = self.request_record(request)
        failure = request.failure
        record["failure"] = failure or "request failed"

    def on_response(self, response: Response) -> None:
        request = response.request
        record = self.request_record(request)
        headers = sanitize_headers(response.headers)
        content_type = response.headers.get("content-type", "")
        record["status"] = response.status
        record["response_headers"] = headers
        record["response_content_type"] = content_type
        if not host_matches(response.url, self.host_filter):
            return
        if not is_textual_response(content_type, request.resource_type):
            record["response_summary"] = {"skipped": f"non-text {request.resource_type}"}
            return
        try:
            body = response.body()
        except PlaywrightError as exc:
            record["response_summary"] = {"body_error": str(exc)[:240]}
            return
        text = decode_body(body, content_type)
        trimmed = text[: self.max_body_chars]
        record["response_summary"] = compact_body_summary(trimmed, content_type)
        if text:
            body_path = self.output_dir / "bodies" / f"{record['id']:04d}_{safe_file_stem(record['path'])}.txt"
            ensure_dir(body_path.parent)
            body_path.write_text(redact_text(trimmed), encoding="utf-8")
            record["response_body_file"] = str(body_path.relative_to(self.output_dir))

    def write_outputs(self, console_messages: list[dict[str, Any]], page_info: dict[str, Any]) -> None:
        ensure_dir(self.output_dir)
        jsonl_path = self.output_dir / "network_records.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as fp:
            for record in self.records:
                fp.write(json_dumps(record) + "\n")

        summary = build_summary(self.records, console_messages=console_messages, page_info=page_info)
        (self.output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.output_dir / "report.md").write_text(render_markdown_report(summary), encoding="utf-8")


def safe_file_stem(path: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", path.strip("/") or "root")
    return stem[-120:] or "response"


def build_summary(
    records: list[dict[str, Any]],
    *,
    console_messages: list[dict[str, Any]],
    page_info: dict[str, Any],
) -> dict[str, Any]:
    post_records = [r for r in records if str(r.get("method")).upper() == "POST"]
    json_records = [
        r for r in records
        if "json" in str(r.get("response_content_type") or "").lower()
        or r.get("response_summary", {}).get("json")
    ]
    interesting = []
    for record in records:
        path = str(record.get("path") or "")
        summary_json = record.get("response_summary", {}).get("json") or {}
        form_fields = record.get("post_data", {}).get("fields") or {}
        if (
            str(record.get("method")).upper() == "POST"
            or summary_json
            or any(marker in path for marker in ("kbcx", "kbdy", "sykbcx", "xtgl"))
        ):
            interesting.append(
                {
                    "id": record.get("id"),
                    "method": record.get("method"),
                    "status": record.get("status"),
                    "path": path,
                    "query": record.get("query"),
                    "resource_type": record.get("resource_type"),
                    "form_fields": form_fields,
                    "response_content_type": record.get("response_content_type"),
                    "response_json": summary_json,
                    "response_preview": record.get("response_summary", {}).get("preview", "")[:360],
                    "body_file": record.get("response_body_file", ""),
                    "failure": record.get("failure", ""),
                }
            )

    inferred_endpoints = []
    for record in interesting:
        path = str(record.get("path") or "")
        if not path:
            continue
        inferred_endpoints.append(
            {
                "id": record.get("id"),
                "method": record.get("method"),
                "status": record.get("status"),
                "path": path,
                "query": record.get("query"),
                "form_keys": list((record.get("form_fields") or {}).keys()),
                "json_keys": (record.get("response_json") or {}).get("keys", []),
                "array_lengths": (record.get("response_json") or {}).get("array_lengths", {}),
                "samples": (record.get("response_json") or {}).get("samples", {}),
            }
        )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "page": page_info,
        "counts": {
            "requests": len(records),
            "posts": len(post_records),
            "json_responses": len(json_records),
            "console_messages": len(console_messages),
        },
        "inferred_endpoints": inferred_endpoints,
        "interesting_records": interesting,
        "console_messages": console_messages[-80:],
    }


def render_markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Academic Browser Probe Report",
        "",
        f"- Generated at: `{summary.get('generated_at', '')}`",
        f"- Final URL: `{summary.get('page', {}).get('url', '')}`",
        f"- Title: `{summary.get('page', {}).get('title', '')}`",
        f"- Requests: `{summary.get('counts', {}).get('requests', 0)}`",
        f"- POST requests: `{summary.get('counts', {}).get('posts', 0)}`",
        f"- JSON responses: `{summary.get('counts', {}).get('json_responses', 0)}`",
        "",
        "## Inferred Endpoints",
        "",
    ]
    endpoints = summary.get("inferred_endpoints") or []
    if not endpoints:
        lines.append("No matching endpoints were captured.")
    for endpoint in endpoints:
        form_keys = ", ".join(endpoint.get("form_keys") or [])
        json_keys = ", ".join(endpoint.get("json_keys") or [])
        arrays = json_dumps(endpoint.get("array_lengths") or {})
        lines.extend(
            [
                f"### {endpoint.get('id')} `{endpoint.get('method')} {endpoint.get('path')}`",
                "",
                f"- Status: `{endpoint.get('status')}`",
                f"- Query: `{endpoint.get('query') or ''}`",
                f"- Form keys: `{form_keys}`",
                f"- JSON keys: `{json_keys}`",
                f"- Array lengths: `{arrays}`",
                "",
            ]
        )
    lines.extend(["## Console Messages", ""])
    for message in summary.get("console_messages") or []:
        lines.append(f"- `{message.get('type')}` {message.get('text')}")
    return "\n".join(lines).strip() + "\n"


def attach_capture(page: Page, store: CaptureStore, console_messages: list[dict[str, Any]]) -> None:
    page.on("request", store.on_request)
    page.on("response", store.on_response)
    page.on("requestfailed", store.on_request_failed)
    page.on(
        "console",
        lambda msg: console_messages.append(
            {
                "type": msg.type,
                "text": redact_text(msg.text)[:1000],
                "location": msg.location,
            }
        ),
    )
    page.on(
        "pageerror",
        lambda exc: console_messages.append(
            {
                "type": "pageerror",
                "text": redact_text(str(exc))[:1000],
                "location": {},
            }
        ),
    )


def fill_login_form(page: Page, username: str, password: str) -> bool:
    try:
        page.goto(DEFAULT_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.locator("input[name='yhm'], #yhm").first.fill(username, timeout=10000)
        page.locator("input[name='mm'], #mm, input[type='password']").first.fill(password, timeout=10000)
        submit = page.locator("#dl, button[type='submit'], input[type='submit'], .btn-primary").first
        if submit.count():
            submit.click(timeout=10000)
        else:
            page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle", timeout=30000)
        return True
    except PlaywrightError:
        return False


def trigger_query_if_available(page: Page) -> bool:
    selectors = [
        "#search_go",
        "#btn_query",
        "button:has-text('查询')",
        "a:has-text('查询')",
        ".btn-primary:has-text('查询')",
        ".btn:has-text('查询')",
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=1500):
                locator.click(timeout=8000)
                page.wait_for_timeout(1200)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightError:
                    pass
                return True
        except PlaywrightError:
            continue
    return False


def create_context(args: argparse.Namespace) -> tuple[Browser | None, BrowserContext]:
    browser_executable = args.browser_executable or default_browser_executable()
    launch_kwargs: dict[str, Any] = {
        "headless": args.headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if browser_executable:
        launch_kwargs["executable_path"] = browser_executable

    playwright = sync_playwright().start()
    if args.user_data_dir:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=args.user_data_dir,
            viewport={"width": args.width, "height": args.height},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            ignore_https_errors=True,
            **launch_kwargs,
        )
        context._playwright_owner = playwright  # type: ignore[attr-defined]
        return None, context

    browser = playwright.chromium.launch(**launch_kwargs)
    storage_state = args.storage_state if args.storage_state and Path(args.storage_state).exists() else None
    context = browser.new_context(
        viewport={"width": args.width, "height": args.height},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        ignore_https_errors=True,
        storage_state=storage_state,
    )
    context._playwright_owner = playwright  # type: ignore[attr-defined]
    return browser, context


def close_context(browser: Browser | None, context: BrowserContext) -> None:
    playwright = getattr(context, "_playwright_owner", None)
    try:
        context.close()
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use a real browser to inspect Guangxi Foreign Languages University JWXT "
            "requests and responses. This is a diagnostic tool only."
        )
    )
    parser.add_argument("--url", default=DEFAULT_TARGET_URL, help="Target page to visit after login.")
    parser.add_argument("--output-dir", default="", help="Directory for JSONL, body files, and reports.")
    parser.add_argument("--host-filter", default="jwxt.gxufl.com", help="Only store response bodies for this host.")
    parser.add_argument("--browser-executable", default="", help="Path to Chrome/Edge executable.")
    parser.add_argument("--storage-state", default="", help="Playwright storage state JSON for authenticated reuse.")
    parser.add_argument("--user-data-dir", default="", help="Persistent browser profile directory for manual login reuse.")
    parser.add_argument("--username", default=os.environ.get("JWXT_USERNAME", ""), help="JWXT username; env JWXT_USERNAME also works.")
    parser.add_argument("--password", default=os.environ.get("JWXT_PASSWORD", ""), help="JWXT password; env JWXT_PASSWORD also works.")
    parser.add_argument("--manual-login-seconds", type=int, default=0, help="Wait this many seconds for manual login before visiting target.")
    parser.add_argument("--settle-seconds", type=float, default=8.0, help="Extra wait after page network idle for late AJAX requests.")
    parser.add_argument("--max-body-chars", type=int, default=200000, help="Maximum text chars stored per response body.")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--headless", action="store_true", help="Run browser headless. Default is headed for easier manual login.")
    parser.add_argument("--skip-query-click", action="store_true", help="Do not click the timetable query button after navigation.")
    parser.add_argument("--save-storage-state", default="", help="Save authenticated storage state to this JSON path.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir or Path(".codex-temp") / "academic-browser-probe" / now_stamp())
    ensure_dir(output_dir)

    console_messages: list[dict[str, Any]] = []
    browser: Browser | None = None
    context: BrowserContext | None = None
    try:
        browser, context = create_context(args)
        page = context.new_page()
        store = CaptureStore(output_dir=output_dir, host_filter=args.host_filter, max_body_chars=args.max_body_chars)
        attach_capture(page, store, console_messages)

        credentials_available = bool(args.username and args.password)
        if credentials_available:
            login_ok = fill_login_form(page, args.username, args.password)
            console_messages.append({"type": "probe", "text": f"automatic login attempted: {login_ok}", "location": {}})
        elif args.manual_login_seconds > 0:
            page.goto(DEFAULT_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            console_messages.append(
                {
                    "type": "probe",
                    "text": f"manual login window opened for {args.manual_login_seconds} seconds",
                    "location": {},
                }
            )
            page.wait_for_timeout(args.manual_login_seconds * 1000)

        page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=45000)
        except PlaywrightError:
            console_messages.append({"type": "probe", "text": "networkidle wait timed out", "location": {}})
        if not args.skip_query_click:
            clicked = trigger_query_if_available(page)
            console_messages.append({"type": "probe", "text": f"query click attempted: {clicked}", "location": {}})
        if args.settle_seconds > 0:
            page.wait_for_timeout(int(args.settle_seconds * 1000))

        if args.save_storage_state:
            state_path = Path(args.save_storage_state)
            ensure_dir(state_path.parent)
            context.storage_state(path=str(state_path))

        page_info = {
            "url": page.url,
            "title": page.title(),
            "target_url": args.url,
            "credentials_available": credentials_available,
            "manual_login_seconds": args.manual_login_seconds,
        }
        store.write_outputs(console_messages=console_messages, page_info=page_info)
        print(f"Academic browser probe complete: {output_dir}")
        print(f"Report: {output_dir / 'report.md'}")
        return 0
    finally:
        if context is not None:
            close_context(browser, context)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
