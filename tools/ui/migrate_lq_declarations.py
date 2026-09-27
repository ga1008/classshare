"""One-time, reviewable declaration migration; never used at application runtime.

Adds explicit shared leaf contracts without replacing nodes/children/attributes.
Source audit and browser acceptance are still required; this is not acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import time

from audit_lq_components import ROOT, ATTR, COMMENTS, exclude_text_sinks, js_without_comments, mask, tags, value, tag_kind

# Files with active manual factory work, generated code, and shared definitions
# are handled by their owners rather than this source-only migration.
MANUAL = {"static/js/ui_color_picker.js", "static/js/whiteboard/toolbar.js",
          "static/js/whiteboard/panels/controls.js", "static/js/whiteboard/popover.js",
          "static/js/lessondoc_editor/ui.js"}
MANUAL.update({"static/js/lessondoc_editor/" + name + ".js" for name in
               ("ai_panel", "content_controls", "index", "media_picker", "canvas_controller")})
MANUAL.update({"static/js/whiteboard/" + name + ".js" for name in ("exam_board", "board", "text_editor")})
MANUAL.add("static/js/approval_workflow.js")
VARIANTS = {
    "btn-primary": "prominent", "btn-danger": "destructive", "btn-danger-solid": "destructive",
    "btn-ghost": "ghost", "btn-outline": "glass", "btn-secondary": "glass", "btn-link": "link",
    "ls-button-primary": "prominent", "ls-link": "link", "lp-btn--primary": "prominent",
    "lp-btn--ghost": "ghost", "lp-btn--danger": "destructive", "cse-btn--primary": "prominent",
    "cse-btn--danger": "destructive",
}

# Reviewed material boundaries: roots used only for placement/scrims stay
# layout/layer components. The visible panel alone owns the raised material.
RAISED = set("""modal-content modal-box export-dialog wrong-answer-dialog knowledge-detail-dialog
academic-evaluation-modal__dialog afm-dialog__panel att-dialog class-student-drawer class-student-modal
course-ai-dialog life-tip-modal offering-hub-drawer academic-modal edu-sync-modal gw-modal
smart-classroom-modal um-modal academic-sync-dialog academic-sync-detail-dialog classroom-group-qr-dialog
teacher-onboarding-dialog career-modal__panel poll-overlay__shell collab-overlay__card resource-mode-dialog
ls-mat-popup__dialog ls-mat-confirm__dialog ga-modal peer-eval-modal material-mastery-dialog lp-modal
semester-todo-modal-card spm-popover tsf-modal chat-message-menu chat-emoji-popover
smart-attendance-detail-popover scoring-question-menu tag-dropdown-menu tag-popover rz-combo__menu
onboarding-lesson-menu ls-tools-menu-body app-topbar-menu__panel dashboard-evaluation-menu__popover
materials-upload-dropdown assignment-more-menu course-lesson-menu offering-schedule-menu
exam-topbar-more-dropdown exam-reverse-dropdown""".split())


def region(tag: str, attrs: dict, snippet: str):
    if "data-lq-component" in attrs or tag not in ("div", "section", "article", "aside", "nav", "header", "footer", "ul", "details", "dialog", "form"): return None
    cls = value(attrs, "class")
    if not cls or re.search(r"\blq-(?:surface|glass|card|page-head|filter-bar|dialog|sheet|drawer|popover|menu|shell)\b", cls): return None
    tokens = set(re.findall(r"[a-z][a-z0-9_-]+", cls))
    # Compound app widgets already have domain-owned portals and motion. Keep
    # their exact shell until their owner explicitly adopts the boundary.
    if any(t.startswith(("ai-workspace", "awb-", "cse-drawer")) for t in tokens): return None
    kind = tag_kind(tag, attrs)
    material = ""
    if tokens & RAISED:
        kind, classes, material = "surface", ["lq-surface", "lq-domain-raised"], ' data-lq-material="raised"'
    elif kind == "surface": kind, classes = "surface", ["lq-surface"]
    elif kind == "toolbar": kind, classes = "toolbar", ["lq-domain-toolbar"]
    elif kind in ("dialog", "menu"):
        kind, classes = ("layer" if kind == "dialog" else "menu"), ["lq-domain-region"]
    else: return None
    existing = next((m for m in ATTR.finditer(snippet) if m[1] == "class"), None)
    if not existing or existing[2][0] not in "\"'": return None
    quote = existing[2][0]
    replacement = f'class={quote}{" ".join(classes)} {cls}{quote}'
    result = snippet[:existing.start()] + replacement + snippet[existing.end():]
    if "data-lq-material" in attrs: material = ""
    result = result[:len(tag)+1] + f' data-lq-component="{kind}"{material}' + result[len(tag)+1:]
    return kind, result


def declare(tag: str, attrs: dict, snippet: str) -> tuple[str, str] | None:
    cls = value(attrs, "class")
    if "data-lq-component" in attrs or re.search(r"\blq-(?:btn|input|textarea|select|checkbox|radio|range|switch|tabs__tab|menu__item)\b", cls):
        return None
    typ = value(attrs, "type").lower()
    if tag == "input" and typ == "hidden": return None
    is_link_control = tag == "a" and bool(re.search(r"\b(?:btn(?:-\w+)?|[\w-]*-btn(?:--\w+)?|ls-button(?:-\w+)?|ls-link|app-topbar-action|[\w-]+__item)\b", cls))
    if tag not in ("button", "input", "textarea", "select", "summary") and not is_link_control: return None
    classes, extra = [], ""
    if tag == "summary":
        kind, classes = "disclosure", ["lq-disclosure-trigger"]
    elif tag in ("button", "a") or tag == "input" and typ in ("button", "submit", "reset"):
        choice = bool(re.search(r"(?:choice|candidate|swatch|color-chip|layout-option|week-btn|week-item|modal-option|font-option)", cls))
        kind = "choice" if choice else "button"
        classes = ["lq-btn", "lq-btn--sm"]
        if choice: classes.append("lq-domain-choice")
        # Keep conditional variants in their original Jinja/JS branches.
        has_variant = False
        def variant(m):
            nonlocal has_variant
            token = m.group()
            if token not in VARIANTS: return token
            has_variant = True
            return token + " lq-btn--" + VARIANTS[token]
        cls = re.sub(r"(?<![\w-])[a-z][a-z0-9_-]*(?![\w-])", variant, cls)
        if not has_variant: classes.append("lq-btn--glass")
        if re.search(r"(?:swatch|color-chip)", cls): extra += ' data-lq-visual="color"'
    else:
        kind = tag if tag != "input" else typ if typ in ("checkbox", "radio", "range") else "input"
        # Native file/color pickers retain their OS dialog and data geometry.
        if tag == "input" and typ in ("file", "color"):
            kind, classes = typ, [f"lq-native-{typ}"]
        else: classes = [f"lq-{kind}"]
    joined = " ".join([*classes, cls]).strip()
    existing = next((m for m in ATTR.finditer(snippet) if m[1] == "class"), None)
    if existing:
        quote = existing[2][0]
        if quote not in "\"'": return None
        replacement = f"class={quote}{joined}{quote}"
        result = snippet[:existing.start()] + replacement + snippet[existing.end():]
    else:
        result = snippet[:len(tag)+1] + f' class="{joined}"' + snippet[len(tag)+1:]
    result = result[:len(tag)+1] + f' data-lq-component="{kind}"{extra}' + result[len(tag)+1:]
    return kind, result


def migrate(file: str, source: str, regions=False):
    if file.endswith(".html"):
        cleaned = COMMENTS.sub(lambda m: mask(m.group()), source)
        cleaned = re.sub(r"<style\b[^>]*>.*?</style\s*>", lambda m: mask(m.group()), cleaned, flags=re.S|re.I)
    else:
        cleaned = exclude_text_sinks(js_without_comments(source))
    changes = []
    for at, tag, attrs, snippet in tags(cleaned):
        if result := (region if regions else declare)(tag, attrs, snippet):
            kind, replacement = result
            changes.append((at, len(snippet), replacement, kind))
    updated = source
    for at, length, replacement, _ in reversed(changes): updated = updated[:at] + replacement + updated[at + length:]
    return updated, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--regions", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    audit = json.loads((ROOT / "docs/lq-platform-component-audit-2026-09-27.json").read_text(encoding="utf-8"))
    files = sorted({e["file"] for e in audit["entries"] if e["syntax"] in ("jinja-html", "js-html-candidate")})
    records, unresolved = [], []
    for file in files:
        if file in MANUAL or file.startswith(("static/js/lq/", "templates/macros/lq/", "templates/dev/")) or ".min." in file: continue
        path = ROOT / file
        source = path.read_text(encoding="utf-8")
        updated, changes = migrate(file, source, args.regions)
        if changes:
            if file.endswith(".js"):
                check = subprocess.run(["node", "--check", "--input-type=module"], input=updated, text=True, encoding="utf-8", capture_output=True)
                if check.returncode:
                    unresolved.append({"file": file, "reason": "requires manual escaped-string migration", "diagnostic": check.stderr[:1000]})
                    continue
            records.append({"file": file, "declarations": len(changes), "kinds": dict(Counter(item[3] for item in changes))})
            if args.write:
                # A sync client can briefly hold a Windows file during save.
                # Retry the exact owned path; never skip a failed write silently.
                for attempt in range(5):
                    try:
                        path.write_text(updated, encoding="utf-8", newline="\n")
                        break
                    except OSError:
                        if attempt == 4: raise
                        time.sleep(.2)
    report = {"write": args.write, "fileCount": len(records), "declarationCount": sum(r["declarations"] for r in records), "files": records, "unresolved": unresolved}
    if args.report: args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "files"}))


if __name__ == "__main__": main()
