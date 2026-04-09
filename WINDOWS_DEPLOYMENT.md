# Windows Deployment

## Single-file installer

Build a standard Windows installer EXE:

```powershell
build_windows_installer.bat
```

Output:

- `dist/installer/LanShare-Setup.exe`

The installer will:

1. install the app into `%LOCALAPPDATA%\Programs\LanShare`,
2. preserve writable user content on upgrade,
3. create a desktop shortcut `LanShare`,
4. create Start Menu shortcuts for start, stop and uninstall,
5. optionally launch the application after installation.

The installer is built with Windows built-in `IExpress`, so it does not depend on Inno Setup or WiX.

## One-click startup

Double-click `start_lanshare.bat`.

The launcher will:

1. validate that the machine is running 64-bit Windows 10/11,
2. create `.env` from `.env.example` when missing,
3. prepare or repair `python_runtime`,
4. install locked dependencies when the runtime is out of date,
5. start `ai_assistant.py` first and then `main.py`,
6. wait for both health endpoints,
7. open the teacher login page in the default browser.

`stop_lanshare.bat` stops the managed processes.

`repair_runtime.bat` repairs `python_runtime` without starting services.

## Existing single-service scripts

`start_main_app.bat` starts only the main FastAPI service.

`start_ai_assistant.bat` starts only the AI service.

Both scripts now use the same runtime resolver and health-aware launcher.

## Packaging a distributable folder

Run:

```powershell
build_windows_package.bat
```

The builder creates:

- `dist/windows-package/lanshare-win64/`
- `dist/windows-package/lanshare-win64.zip`

By default the package includes the current `.env` file and mutable project data folders.

Useful flags:

```powershell
build_windows_package.bat --exclude-user-data --exclude-env
build_windows_package.bat --skip-runtime --no-zip
```

## Installer notes

- The installed shortcuts start the app through `pythonw.exe` so no console window is shown.
- Logs are written to `logs/launcher.log`, `logs/main.log` and `logs/ai.log` inside the install directory.
- Uninstall is available from the Start Menu shortcut `卸载 LanShare`.

## Upgrade strategy

The launcher tracks:

- app version from `deployment/metadata.json`,
- dependency hash from `requirements.lock.txt`,
- runtime state in `data/runtime_state.json`.

After replacing the application files with a newer package, running `start_lanshare.bat` or `repair_runtime.bat` will automatically resync `python_runtime` when the version or dependency hash changes.

## Notes

- `venv` is still accepted as an interpreter candidate, but it is no longer trusted blindly.
- `python_runtime` is the preferred runtime because it is copied from a full Python home and then synchronized with locked dependencies.
- Health checks are exposed at `/api/internal/health` on both services.
