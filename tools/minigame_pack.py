"""Build or statically check a standalone LanShare game. No database or browser required."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from classroom_app.services import minigame_package_validation as check

GENERATED = {"js/lan-game-sdk.v1.js", "js/offline-contracts.js"}


def inputs(source):
    for filename in ("main.html", "config.json"):
        yield source / filename
    for folder in ("js", "css", "static", "contracts"):
        top = source / folder
        if top.is_symlink() or top.is_junction():
            raise ValueError(f"Source folder must be local: {folder}")
        if not top.exists():
            continue
        for directory, children, names in os.walk(top, followlinks=False):
            base = Path(directory)
            for child in children:
                path = base / child
                if path.is_symlink() or path.is_junction():
                    raise ValueError(f"Linked directory: {path.relative_to(source)}")
            for name in names:
                yield base / name


def build(source, output, replace=False):
    source = source.resolve(strict=True)
    output = output.absolute()
    if not source.is_dir() or output.suffix.lower() != ".zip":
        raise ValueError("Choose a game source directory and a .zip output file")
    if output.exists() and not replace:
        raise FileExistsError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    members = {}
    total = 0
    for path in inputs(source):
        rel = path.relative_to(source).as_posix()
        normalized = check.clean_member(rel)
        if (
            path.is_symlink()
            or path.is_junction()
            or not path.is_file()
            or not path.resolve().is_relative_to(source)
        ):
            raise ValueError(f"Not a regular source file: {rel}")
        check.member_type(normalized)
        if normalized not in GENERATED:
            if normalized in members:
                raise ValueError(f"Duplicate entry: {normalized}")
            size = path.stat().st_size
            total += size
            if size > check.MAX_FILE or total > check.MAX_TOTAL or len(members) >= 1000:
                raise ValueError("Source exceeds the game package quota")
            members[normalized] = path

    def contract(name):
        if name not in members:
            raise ValueError(f"Missing contract: {name}")
        if members[name].stat().st_size > check.TEXT_LIMIT:
            raise ValueError(f"Contract too large: {name}")
        return check.parse_json(members[name].read_text(encoding="utf-8-sig"), name)

    config = contract("config.json")
    check.validate_schema(config, "config", "config.json")
    rules = config["contracts"]["rules"]
    offline = {
        "config": config,
        "writeback": contract(config["contracts"]["writeback"]),
        "rules": contract(rules) if rules else None,
    }
    with TemporaryDirectory(prefix=".game-build-", dir=output.parent) as scratch:
        work = Path(scratch)
        temp_zip = work / "package.zip"
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as pack:
            for name, path in sorted(members.items()):
                pack.write(path, name)
            pack.write(check.SDK, "js/lan-game-sdk.v1.js")
            pack.writestr(
                "js/offline-contracts.js",
                "window.LanGameOfflineContracts = " + check.canonical(offline) + ";\n",
            )
        result = check.validate_package(temp_zip, work)
        if replace:
            os.replace(temp_zip, output)
        else:
            # Keep the output exclusive even when two author processes select the same file.
            with output.open("xb") as target:
                try:
                    with temp_zip.open("rb") as source_zip:
                        shutil.copyfileobj(source_zip, target)
                    target.flush()
                except BaseException:
                    target.close()
                    output.unlink(missing_ok=True)
                    raise
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    spec = commands.add_parser(
        "pack",
        help="Generate the SDK and offline contracts, then build and validate the ZIP",
    )
    spec.add_argument("source", type=Path)
    spec.add_argument("output", type=Path)
    spec.add_argument("--replace", action="store_true")
    inspect = commands.add_parser(
        "validate", help="Check the bytes and contracts of an existing ZIP"
    )
    inspect.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "pack":
            result = build(args.source, args.output, args.replace)
        else:
            with TemporaryDirectory(prefix="lanshare-game-check-") as scratch:
                result = check.validate_package(args.package, Path(scratch))
    except check.InvalidPackage as exc:
        print(json.dumps(exc.issue, ensure_ascii=False), file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "staticValidation": "passed",
                "gameKey": result["config"]["gameKey"],
                "version": result["config"]["version"],
                "files": len(result["files"]),
                "digest": result["digest"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
