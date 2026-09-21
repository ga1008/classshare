"""Compare recorded page/viewport baselines without treating dynamic pixels as proof."""
import argparse
import json
from pathlib import Path


def compare(before: list[Path], after: Path) -> dict:
    old = {(p["role"], p["name"], p["width"]): p for source in before for p in json.loads(source.read_text(encoding="utf-8"))["pages"]}
    current = json.loads(after.read_text(encoding="utf-8"))
    report = {"beforeCount": len(old), "afterCount": len(current["pages"]), "missing": [], "regressions": [], "existingOverflow": [], "textChanges": [], "unmatchedNewPages": []}
    seen = set()
    for page in current["pages"]:
        key = page["role"], page["name"], page["width"]
        seen.add(key)
        previous = old.get(key)
        if previous is None:
            report["unmatchedNewPages"].append(key)
            continue
        for field in ("status", "url"):
            if previous[field] != page[field]:
                report["regressions"].append({"page": key, "field": field, "before": previous[field], "after": page[field]})
        if page["errors"]:
            report["regressions"].append({"page": key, "errors": page["errors"]})
        if page["scrollWidth"] > page["width"] + 1:
            bucket = "regressions" if page["scrollWidth"] > previous["scrollWidth"] + 1 else "existingOverflow"
            report[bucket].append({"page": key, "beforeWidth": previous["scrollWidth"], "afterWidth": page["scrollWidth"]})
        if page["bodyTextLength"] != previous["bodyTextLength"]:
            report["textChanges"].append({"page": key, "beforeLength": previous["bodyTextLength"], "afterLength": page["bodyTextLength"]})
    report["missing"] = sorted(set(old) - seen)
    report["captureFailures"] = current["failures"]
    report["limitations"] = ["Text length differences require review; they do not establish missing information.", "This compares routes, runtime errors and horizontal overflow. It does not certify pixel/contrast/accessibility parity."]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", nargs="+", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.before, args.after)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: len(value) if isinstance(value, list) else value for key, value in report.items() if key != "limitations"}))
    raise SystemExit(1 if report["regressions"] or report["missing"] or report["captureFailures"] else 0)
