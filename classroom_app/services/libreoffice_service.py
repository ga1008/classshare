"""Safe LibreOffice conversion helpers.

LibreOffice headless conversion is sensitive to the selected executable,
current working directory, and user profile URI on Windows. Keep that handling
centralized so document preview/export paths do not each reinvent it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class LibreOfficeUnavailable(RuntimeError):
    """Raised when no usable LibreOffice command can be found."""


class LibreOfficeConversionError(RuntimeError):
    """Raised when LibreOffice exits without producing the requested output."""


@dataclass(frozen=True)
class LibreOfficeConversion:
    output_bytes: bytes
    output_name: str
    stdout: str
    stderr: str
    command: tuple[str, ...]


def resolve_soffice_command() -> str | None:
    """Resolve LibreOffice, preferring the console launcher on Windows."""

    candidates = (
        ("soffice.com", "soffice.exe", "soffice", "libreoffice")
        if os.name == "nt"
        else ("soffice", "libreoffice")
    )
    for candidate in candidates:
        path = shutil.which(candidate)
        if path:
            return path
    return None


def user_installation_arg(profile_dir: Path) -> str:
    """Build a valid LibreOffice UserInstallation argument for this OS."""

    return f"-env:UserInstallation={profile_dir.resolve().as_uri()}"


def _build_env(profile_dir: Path, temp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Stale bootstrap-related variables can override the installed bootstrap.ini.
    env.pop("URE_BOOTSTRAP", None)
    env.pop("UNO_PATH", None)
    env["HOME"] = str(profile_dir)
    env["XDG_CONFIG_HOME"] = str(profile_dir / "xdg_config")
    env["XDG_CACHE_HOME"] = str(profile_dir / "xdg_cache")
    env["TMP"] = str(temp_dir)
    env["TEMP"] = str(temp_dir)
    (profile_dir / "xdg_config").mkdir(parents=True, exist_ok=True)
    (profile_dir / "xdg_cache").mkdir(parents=True, exist_ok=True)
    return env


def _target_suffix(output_format: str) -> str:
    first = str(output_format or "").split(":", 1)[0].strip().lower().lstrip(".")
    return f".{first or 'pdf'}"


def _short_process_error(stdout: str, stderr: str) -> str:
    text = (stderr or stdout or "").strip()
    return text[:300] if text else "LibreOffice did not report an error"


def convert_office_file(input_path: Path, output_format: str, *, timeout: int = 90) -> LibreOfficeConversion:
    """Convert an Office file with an isolated profile and ASCII temp filename."""

    source_path = Path(input_path)
    if not source_path.exists():
        raise FileNotFoundError(str(source_path))

    soffice = resolve_soffice_command()
    if not soffice:
        raise LibreOfficeUnavailable("当前服务器未安装 LibreOffice，无法执行 Office 转换。")

    target_suffix = _target_suffix(output_format)
    with tempfile.TemporaryDirectory(prefix="lanshare-lo-") as temp_root:
        root = Path(temp_root)
        work_dir = root / "work"
        out_dir = root / "out"
        profile_dir = root / "profile"
        work_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Keep LibreOffice away from Chinese/Nutstore/original upload paths.
        temp_source = work_dir / f"input{source_path.suffix or '.bin'}"
        shutil.copy2(source_path, temp_source)

        command = [
            soffice,
            user_installation_arg(profile_dir),
            "--headless",
            "--invisible",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            output_format,
            "--outdir",
            str(out_dir),
            str(temp_source),
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
            env=_build_env(profile_dir, work_dir),
        )

        candidates = sorted(path for path in out_dir.iterdir() if path.suffix.lower() == target_suffix)
        if completed.returncode != 0 or not candidates:
            raise LibreOfficeConversionError(
                "LibreOffice 转换失败："
                + _short_process_error(completed.stdout, completed.stderr)
            )

        output_path = candidates[0]
        output_bytes = output_path.read_bytes()
        if not output_bytes:
            raise LibreOfficeConversionError("LibreOffice 转换失败：输出文件为空。")

        return LibreOfficeConversion(
            output_bytes=output_bytes,
            output_name=output_path.name,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            command=tuple(command),
        )


def convert_docx_bytes_to_pdf(docx_content: bytes, *, timeout: int = 90) -> bytes:
    """Convert DOCX bytes to PDF bytes through the safe LibreOffice path."""

    with tempfile.TemporaryDirectory(prefix="lanshare-docx-pdf-") as temp_root:
        input_path = Path(temp_root) / "input.docx"
        input_path.write_bytes(docx_content)
        return convert_office_file(input_path, "pdf", timeout=timeout).output_bytes
