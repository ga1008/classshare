"""One isolated Chromium smoke test. Run on a dedicated, no-egress Linux worker.

The optional digest allowlist is for a developer's byte-exact, reviewed test
fixture. It cannot be activated by the ZIP or by an HTTP upload request.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import quote, unquote, urlsplit

from playwright.sync_api import Error as BrowserError, sync_playwright, TimeoutError as BrowserTimeout

HOST = "https://host.invalid"
ASSET = "https://content.invalid"
ASSET_CHECK = Path(__file__).with_name("minigame_probe_assets.js")
INSPECTOR = '<!doctype html><meta charset="utf-8"><input type="file">'
MAX_FILE_BYTES, MAX_TREE_BYTES = 32 * 1024**2, 192 * 1024**2
TYPES = {
    "js": "text/javascript",
    "css": "text/css",
    "html": "text/html",
    "json": "application/json",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "woff2": "font/woff2",
}


def manifest(root):
    files = {}
    total = 0
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise ValueError("not a regular tree")
        if entry.is_file():
            total += entry.stat().st_size
            if len(files) >= 1000 or total > MAX_TREE_BYTES or entry.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("input exceeds verified tree quotas")
            relative = entry.relative_to(root).as_posix()
            digest = sha256()
            with entry.open("rb") as handle:
                while chunk := handle.read(256 * 1024):
                    digest.update(chunk)
            files[relative] = {
                "size": entry.stat().st_size,
                "mime": TYPES[entry.suffix[1:].lower()],
                "sha256": digest.hexdigest(),
            }
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return files, sha256(encoded).hexdigest()


def isolated_worker(root):
    if not sys.platform.startswith("linux") or os.getuid() == 0:
        return False
    network = {line.split(":")[0].strip() for line in Path("/proc/net/dev").read_text().splitlines()[2:]}
    return (
        network <= {"lo"}
        and os.environ.get("MINIGAMES_ISOLATED_BROWSER") == "1"
        and not os.access(root, os.W_OK)
        and not os.access(root / "main.html", os.W_OK)
    )


def host_document(contract, nonce):
    # All values stay in a trusted host document. Defuse the script end tag in JSON strings.
    data = json.dumps(contract, ensure_ascii=True).replace("<", "\\u003c")
    return f"""<!doctype html><meta charset=utf-8><iframe title="游戏" sandbox="allow-scripts"></iframe>
<script>
const contracts={data}, nonce={json.dumps(nonce)}, f=document.querySelector('iframe');
const me={{playerId:'probe',displayName:'验收玩家',seat:1}};
const context={{protocol:1,environment:'preview',me,mode:contracts.config.modes.solo?'solo':'private',
  capabilities:contracts.config.capabilities,roomId:'probe',runId:'probe',audio:{{muted:true}}}};
const rules=contracts.rules, first=contracts.writeback.milestones[0];
const state=rules?.engine==='grid-line-v1'?{{board:Array(rules.size**2).fill(0),turnSeat:1,rank:0}}:
  {{node:rules?.initial,turnSeat:1,rank:0}};
const snapshot={{context,me,mode:context.mode,phase:'running',status:'running',state,revision:1,seq:1,
    runId:'probe',roomId:'probe',turnSeat:1,seats:[me],members:[me],serverNow:Date.now(),startRank:0}};
const progress={{rank:0,privateRevision:0,sharedRevision:0,milestoneId:first.id,
    privateSave:Object.fromEntries(Object.entries(contracts.writeback.privateFields).map(([k,v])=>[k,v.default]))}};
window.addEventListener('message', e=>{{
  if(e.source!==f.contentWindow || e.origin!=='null' || e.data?.type!=='lanshare:hello' ||
     e.data?.nonce!==nonce || !e.ports[0]) return;
  window.handshake=true;
  const port=e.ports[0];
  port.onmessage=e=>{{const m=e.data;
    const result=m.method==='progress.read'?progress:m.method==='session.snapshot'?snapshot:
        m.method==='room.members'?snapshot.members:m.method==='history.list'?{{items:[],nextCursor:null}}:{{}};
    port.postMessage({{v:1,id:m.id,ok:true,result}});
  }};
  port.postMessage({{type:'welcome',context,definition:contracts}});
}});
f.onload=()=>f.contentWindow.postMessage({{type:'lanshare:discover',hostOrigin:location.origin}},'*');
f.src='{ASSET}/main.html#ls_host='+encodeURIComponent(location.origin)+'&ls_nonce='+nonce;
</script>"""


def inspect_assets(context, root, files, offline_urls):
    # A separate probe-owned document keeps uploaded game script out of the decoders.
    paths = sorted(name for name in files if name.startswith("static/"))
    decoder = ASSET_CHECK.read_text(encoding="utf-8")
    deadline = time.monotonic() + 14
    counts = {"platform": 0, "offline": 0, "total": len(paths)}
    with TemporaryDirectory(prefix="game-asset-probe-") as scratch:
        local_probe = Path(scratch) / "assets.html"
        local_probe.write_text(INSPECTOR, encoding="utf-8")
        offline_urls.add(local_probe.as_uri())
        for mode in ("platform", "offline"):
            page = context.new_page()
            try:
                offline = mode == "offline"
                page.goto(
                    local_probe.as_uri() if offline else HOST + "/resource-inspection",
                    wait_until="load",
                    timeout=3500,
                )
                frame = page.main_frame if offline else page.frames[-1]
                if not offline and frame.url != ASSET + "/__asset-check.html":
                    raise RuntimeError("probe-owned sandbox failed to load")
                for name in paths:
                    remaining = int((deadline - time.monotonic()) * 1000)
                    if remaining < 100:
                        return counts, {"file": name, "field": mode, "code": "ASSET_PROBE_BUDGET"}
                    meta = files[name]
                    file = root / name
                    if offline and not meta["mime"].startswith("video/"):
                        try:
                            frame.locator("input").set_input_files(file, timeout=min(remaining, 3000))
                        except BrowserTimeout:
                            return counts, {"file": name, "field": mode, "code": "ASSET_PROBE_BUDGET"}
                    url = file.as_uri() if offline else ASSET + "/" + quote(name, safe="/")
                    code = frame.evaluate(
                        decoder,
                        {
                            "url": url,
                            "mime": meta["mime"],
                            "size": meta["size"],
                            "offline": offline,
                            "timeoutMs": min(remaining, 3000),
                        },
                    )
                    if code:
                        return counts, {"file": name, "field": mode, "code": code}
                    counts[mode] += 1
            finally:
                page.close()
    return counts, None


def run(root, files, browser_binary, native_sandbox):
    config = json.loads((root / "config.json").read_text(encoding="utf-8-sig"))
    contract = {
        "config": config,
        "writeback": json.loads((root / config["contracts"]["writeback"]).read_text(encoding="utf-8-sig")),
        "rules": None,
    }
    if config["contracts"]["rules"]:
        contract["rules"] = json.loads((root / config["contracts"]["rules"]).read_text(encoding="utf-8-sig"))
    host_page = host_document(contract, secrets.token_hex(16))
    failures = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            chromium_sandbox=native_sandbox, executable_path=browser_binary, headless=True, timeout=6000
        )
        try:
            # The CSP and opaque origin prevent workers. Playwright's blocking shim reads
            # navigator.serviceWorker in that origin and itself raises a spurious pageerror.
            context = browser.new_context(accept_downloads=False)

            def route(req):
                url = urlsplit(req.request.url)
                origin = f"{url.scheme}://{url.netloc}"
                path = unquote(url.path).lstrip("/")
                if origin == HOST and path == "" and req.request.resource_type == "document":
                    return req.fulfill(status=200, content_type="text/html; charset=utf-8", body=host_page)
                if (
                    origin == HOST
                    and path == "resource-inspection"
                    and req.request.resource_type == "document"
                ):
                    return req.fulfill(
                        status=200,
                        content_type="text/html",
                        body='<iframe sandbox="allow-scripts" src="'
                        + ASSET
                        + '/__asset-check.html"></iframe>',
                    )
                if origin == ASSET and not url.query and (path in files or path == "__asset-check.html"):
                    headers = {
                        "Access-Control-Allow-Origin": "null",
                        "Referrer-Policy": "no-referrer",
                        "X-Content-Type-Options": "nosniff",
                    }
                    if path in ("main.html", "__asset-check.html"):
                        headers["Content-Security-Policy"] = (
                            f"sandbox allow-scripts; default-src 'none'; script-src {ASSET}; "
                            f"style-src {ASSET} 'unsafe-inline'; img-src {ASSET} data: blob:; media-src {ASSET} blob:; "
                            f"font-src {ASSET} data:; connect-src {ASSET}; frame-ancestors {HOST}; "
                            "object-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'"
                        )
                    if path == "__asset-check.html":
                        return req.fulfill(body=INSPECTOR, content_type="text/html", headers=headers)
                    return req.fulfill(path=root / path, content_type=files[path]["mime"], headers=headers)
                if origin == "file://" and req.request.url.split("#")[0] in offline_urls:
                    return req.continue_()
                failures.append("undeclared-" + req.request.resource_type)
                return req.abort()

            offline_urls = {(root / path).as_uri() for path in files}
            context.route("**/*", route)
            page = context.new_page()
            page.set_default_timeout(4500)

            def page_error(exc):
                if not native_sandbox:
                    print("Reviewed fixture script error:", str(exc)[:320], file=sys.stderr)
                failures.append("script-error")

            page.on("pageerror", page_error)
            try:
                page.goto(HOST, wait_until="load")
                page.wait_for_function("window.handshake === true")
                frame = page.frames[-1]
                online = frame.evaluate(
                    "async()=> (await Promise.race([window.LanGame.ready(),new Promise(r=>setTimeout(()=>r({}),3000))])).environment==='preview'"
                )
                page.goto((root / "main.html").as_uri(), wait_until="load")
                offline = page.evaluate(
                    "async()=> (await Promise.race([window.LanGame.ready(),new Promise(r=>setTimeout(()=>r({}),3000))])).environment==='local'"
                )
                page.wait_for_timeout(180)
            except BrowserTimeout:
                return False, False, ["handshake-timeout"], {}, None
            except BrowserError:
                if not browser.is_connected() or page.is_closed():
                    raise
                return False, False, ["sdk-error"], {}, None
            page.close()
            if online and offline and not failures:
                # Inspector/browser crashes are infrastructure errors and must not reject a game.
                counts, problem = inspect_assets(context, root, files, offline_urls)
                return online, offline, failures, counts, problem
            return online, offline, failures, {}, None
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--synthetic-digest", help="Only run this exact independently reviewed synthetic fixture"
    )
    parser.add_argument(
        "--trusted-browser-harness",
        action="store_true",
        help="Windows repository scratch only: run the byte-pinned reviewed fixture when nested sandbox startup is unavailable",
    )
    parser.add_argument("--browser", help="Installed worker-owned Chromium executable")
    parser.add_argument("runtime", type=Path)
    parser.add_argument("digest")
    args = parser.parse_args()
    root = args.runtime.resolve(strict=True)
    files, actual = manifest(root)
    if actual != args.digest or (args.synthetic_digest != actual and not isolated_worker(root)):
        return 2
    if args.trusted_browser_harness:
        workspace = (Path(__file__).resolve().parents[1] / ".codex-temp").resolve()
        if os.name != "nt" or args.synthetic_digest != actual or not root.is_relative_to(workspace):
            return 2
    online, offline, issues, counts, resource_issue = run(
        root, files, args.browser, not args.trusted_browser_harness
    )
    ok = bool(online and offline and not issues and not resource_issue)
    isolation = (
        "trusted-fixture"
        if args.trusted_browser_harness
        else "pinned-fixture-native-sandbox"
        if args.synthetic_digest
        else "isolated-worker"
    )
    print(
        json.dumps(
            {
                "status": "passed"
                if ok
                else "unavailable"
                if resource_issue and resource_issue["code"] == "ASSET_PROBE_BUDGET"
                else "invalid",
                "digest": actual,
                "platform": online,
                "offline": offline,
                "checks": sorted(set(issues))[:4],
                "isolation": isolation,
                "resourceCounts": counts,
                "resourceError": resource_issue,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
