"""Isolated real Office/cache acceptance: distinct frozen resumes, then repeats.

Uses the production template and export_resume_cached without replacing the
converter. No database or student file is read. Run only after code/template
freeze, separately from browser/export QA to avoid sharing the Office budget.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz
import psutil
from classroom_app.services import libreoffice_service as office
from classroom_app.services.resume import resume_render_service as render


def fingerprints():
    names = ["tools/resume_real_export_capacity_probe.py",
             "classroom_app/services/libreoffice_service.py",
             "classroom_app/services/document_render_service.py",
             "classroom_app/services/resume/resume_render_service.py",
             "classroom_app/services/resume/resume_profile_service.py"]
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def frozen_resume(index):
    marker = f"QAEXPORTVERSION{index:04d}"
    template = list(render.RESUME_TEMPLATES)[(index - 1) % len(render.RESUME_TEMPLATES)]
    bundle = {
        "personal": {"name": "合成导出样本", "phone": "13800000000", "email": "sample@example.invalid",
                     "expected_position": "跨文化项目协调助理"},
        "education": [{"id": 1, "kind": "university", "school": "合成测试学校", "degree": "本科",
                       "major": "英语", "start_date": "2023-09", "end_date": "2027-06"}],
        "self_intro": [{"id": 1, "content_md": marker + "\n\n本文件仅用于冻结版本导出验收。参与英语沟通、资料核对与活动组织，所有材料均为合成测试数据。"}],
        "experience": [{"id": 1, "kind": "internship", "title": "合成教学实习", "role": "实习成员",
                        "start_date": "2025-03", "end_date": "2025-06", "content": "整理课程资料与课堂反馈。",
                        "contribution": "设计活动记录表，核对反馈信息。", "achievement": "形成教学活动复盘记录。"}],
        "skill": [{"id": 1, "name": "英语沟通"}], "certificate": [],
    }
    resume = {"revision": index, "template_key": template, "target_position": "跨文化项目协调助理",
              "content_snapshot": bundle, "layout": {"personal_fields": ["name", "phone", "email", "expected_position"],
              "blocks": [{"type": "self_intro", "ids": [1]}, {"type": "education", "ids": [1]},
                         {"type": "experience", "ids": [1]}, {"type": "skill_cert", "skill_ids": [1], "cert_ids": []}]}}
    return marker, template, render.assemble_resume_html(None, 0, resume)


@contextmanager
def verify_released_lock(path):
    """A permanent kernel lock file is retained; verify it can be reacquired."""
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run(output: Path, *, versions=20, workers=4, retry_seconds=10, seed=42):
    output.mkdir(parents=True, exist_ok=False)
    cache = output / "cache"
    data_root = output / "data"
    start_hashes = fingerprints()
    documents = [frozen_resume(i) for i in range(1, versions + 1)]
    for marker, _, html in documents:
        (output / (marker + ".html")).write_text(html, encoding="utf-8")
    guard = threading.Lock()
    stop = threading.Event()
    markers, conversions, samples, errors = set(), [], [], []
    original_run = office._run_conversion
    started = time.perf_counter()

    def measured_run(command, **kwargs):
        event = {"profile_arg": command[1], "started_seconds": time.perf_counter() - started}
        with guard:
            markers.add(command[1])
            conversions.append(event)
        try:
            result = original_run(command, **kwargs)
            event["returncode"] = result.returncode
            return result
        finally:
            event["finished_seconds"] = time.perf_counter() - started

    def process_sample():
        with guard:
            known = set(markers)
        found = []
        for process in psutil.process_iter(["pid", "name"], ad_value=None):
            if str(process.info.get("name") or "").lower() not in {"soffice.bin", "soffice.exe", "soffice.com", "soffice", "oosplash"}:
                continue
            try:
                argv = process.cmdline()
                matched = next((value for value in argv if value in known), None)
                if matched and process.status() not in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
                    found.append({"pid": process.pid, "name": process.info["name"], "create_time": process.create_time(), "profile_arg": matched})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {"seconds": round(time.perf_counter() - started, 4), "processes": found,
                "converter_count": sum(p["name"].lower() == "soffice.bin" for p in found),
                "active_profile_count": len({p["profile_arg"] for p in found})}

    def monitor():
        try:
            while not stop.is_set():
                samples.append(process_sample())
                stop.wait(0.05)
        except BaseException as exc:
            errors.append("process_monitor: " + repr(exc))

    def export_one(document):
        marker, template, html = document
        rng = random.Random(seed + int(marker[-4:]))
        busy = {"resume_export": 0, "shared_office": 0}
        beginning = time.perf_counter()
        while True:
            try:
                result = render.export_resume_cached(html, "pdf")
                break
            except (render.ResumeExportBusy, office.LibreOfficeBusy) as exc:
                busy["shared_office" if isinstance(exc, office.LibreOfficeBusy) else "resume_export"] += 1
                if time.perf_counter() - beginning > 900:
                    raise TimeoutError("Export retries exceeded 15 minutes") from exc
                time.sleep(retry_seconds + rng.uniform(0.01, 0.2))
        assert result.startswith(b"%PDF"), "Exporter returned a non-PDF payload"
        with fitz.open(stream=result, filetype="pdf") as pdf:
            text = "\n".join(page.get_text() for page in pdf)
            assert marker in text, (marker, "own version marker absent")
            assert all(other == marker or other not in text for other, _, _ in documents), (marker, "foreign version marker found")
            pages = len(pdf)
        target = output / (marker + ".pdf")
        if target.exists():
            assert target.read_bytes() == result, "Repeated export did not return identical cached bytes"
        else:
            target.write_bytes(result)
        return {"marker": marker, "template": template, "bytes": len(result), "pages": pages,
                "sha256": hashlib.sha256(result).hexdigest(), "seconds": round(time.perf_counter() - beginning, 4), "busy": busy}

    report = {"ok": False, "synthetic_only": True, "database_used": False, "real_office": True,
              "office_executable": office.resolve_soffice_command(), "versions": versions, "request_threads": workers,
              "retry_seconds": retry_seconds, "process_sample_seconds": 0.05, "start_hashes": start_hashes}
    watcher = threading.Thread(target=monitor, name="qa-office-process-sampler", daemon=True)
    try:
        with patch.object(office, "DATA_ROOT", data_root), patch.object(office, "_run_conversion", measured_run), \
                patch.dict(os.environ, {"RESUME_EXPORT_CACHE_DIR": str(cache), "LANSHARE_LIBREOFFICE_MAX_CONCURRENCY": "1"}):
            watcher.start()
            with ThreadPoolExecutor(max_workers=workers) as executor:
                report["first_exports"] = list(executor.map(export_one, documents))
                report["conversions_after_first"] = len(conversions)
                report["repeat_exports"] = list(executor.map(export_one, documents))
            report["conversions_after_repeat"] = len(conversions)
            assert len(conversions) == versions, "A repeat export reconverted or a first export was missing"
            stop.set()
            watcher.join(timeout=5)
            assert not watcher.is_alive(), "Process monitor did not stop"
            report["final_processes"] = process_sample()["processes"]
            lock_root = data_root / "tmp/libreoffice/_locks"
            report["active_slot_metadata"] = [str(p) for p in lock_root.glob("*.json")]
            report["temporary_write_files"] = [str(p) for p in cache.rglob(".export-*")] + [str(p) for p in lock_root.glob("*.tmp")]
            for path in [cache / "conversion.lock", *lock_root.glob("*.lock")]:
                with verify_released_lock(path):
                    pass
            report["permanent_lock_files_reacquirable"] = True
            report["max_actual_converter_processes"] = max((s["converter_count"] for s in samples), default=0)
            report["max_actual_active_profiles"] = max((s["active_profile_count"] for s in samples), default=0)
            report["max_loader_and_converter_processes"] = max((len(s["processes"]) for s in samples), default=0)
            assert report["max_actual_converter_processes"] == 1, "Expected sampled actual soffice.bin concurrency of exactly one"
            assert report["max_actual_active_profiles"] == 1, "More than one conversion profile ran concurrently"
            assert not report["final_processes"] and not report["active_slot_metadata"] and not report["temporary_write_files"]
            assert not errors, errors
            report["ok"] = True
    except BaseException as exc:
        errors.append(repr(exc))
    finally:
        stop.set()
        if watcher.ident:
            watcher.join(timeout=5)
        report.update(seconds=round(time.perf_counter() - started, 3), errors=errors, conversions=conversions,
                      end_hashes=fingerprints(), process_samples=len(samples))
        report["fixed_code"] = report["start_hashes"] == report["end_hashes"]
        report["ok"] = report["ok"] and report["fixed_code"]
        report["busy_total"] = sum(sum(item["busy"].values()) for key in ("first_exports", "repeat_exports") for item in report.get(key, []))
        report["measurement_scope"] = "Real local LibreOffice PDFs through production frozen templates/cache. Only QA-owned profile processes sampled. soffice.com/exe launchers are reported separately from the heavy soffice.bin converter. Permanent lock files remain by design; active OS locks and metadata must clear. Desktop evidence is not a production SLA."
        (output / "office-process-samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "export-capacity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--versions", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retry-seconds", type=float, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.versions < 1 or not 1 <= args.workers <= 20 or args.retry_seconds <= 0:
        parser.error("versions>=1, workers=1..20, retry-seconds>0 required")
    result = run(args.output.resolve(), versions=args.versions, workers=args.workers, retry_seconds=args.retry_seconds, seed=args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)
