# Academic Browser Probe

`academic_browser_probe.py` is a diagnostic crawler for exploring the real
Guangxi Foreign Languages University JWXT browser traffic. It is intentionally
kept under `tools/` and is not imported by the application runtime.

## Install

```powershell
venv\Scripts\python.exe -m pip install -r tools\academic_browser_probe_requirements.txt
venv\Scripts\python.exe -m playwright install chromium
```

If Playwright cannot download Chromium, the script automatically tries the
local Chrome or Edge executable. You can also pass:

```powershell
--browser-executable "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

## Run Without Credentials

```powershell
venv\Scripts\python.exe tools\academic_browser_probe.py --headless
```

This verifies the crawler and captures the unauthenticated redirect/login flow.

## Run With Environment Credentials

```powershell
$env:JWXT_USERNAME="your-jwxt-account"
$env:JWXT_PASSWORD="your-jwxt-password"
venv\Scripts\python.exe tools\academic_browser_probe.py `
  --headless `
  --output-dir .codex-temp\academic-browser-probe\jwxt-authenticated
```

The tool redacts password, token, session, cookie, and username-like values in
saved request headers and payloads.

After opening the timetable page, the tool tries to click the visible `查询`
button so the real AJAX timetable requests are emitted. Use
`--skip-query-click` for passive capture only.

## Run With Manual Login

```powershell
venv\Scripts\python.exe tools\academic_browser_probe.py `
  --manual-login-seconds 120 `
  --user-data-dir .codex-temp\academic-browser-probe\profile `
  --save-storage-state .codex-temp\academic-browser-probe\jwxt-state.json
```

Log in inside the visible browser window. After the wait, the tool visits the
teacher timetable page and records the network traffic.

## Outputs

Each run writes:

- `network_records.jsonl`: sanitized request/response records.
- `summary.json`: structured endpoint summary.
- `report.md`: readable endpoint report.
- `bodies/*.txt`: capped textual response bodies for JSON/HTML/JS responses.

The default target page is:

`https://jwxt.gxufl.com/kbcx/jskbcx_cxJskbcxIndex.html?doType=details&gnmkdm=N2150&layout=default`
