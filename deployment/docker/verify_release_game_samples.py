"""Verify that portable game examples distributed by a release match their reviewed Git blobs."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import tarfile


def verify(stream, size, expected, name):
    digest = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
    while chunk := stream.read(65536):
        digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"Game sample differs from the reviewed Git blob: {name}")


def main():
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--archive", type=Path)
    target.add_argument("--directory", type=Path)
    parser.add_argument("manifest", help="Base64-encoded JSON supplied by the trusted release script")
    args = parser.parse_args()
    expected = json.loads(base64.b64decode(args.manifest, validate=True))
    if args.archive is None:
        for name, blob in expected.items():
            path = args.directory / name
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"Missing regular game sample file: {name}")
            with path.open("rb") as stream:
                verify(stream, path.stat().st_size, blob, name)
    else:
        seen = set()
        with tarfile.open(args.archive, "r|gz") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                if not name.lower().endswith(".zip"):
                    continue
                if name not in expected or name in seen or not member.isfile():
                    raise ValueError(f"Unexpected, repeated or nonregular ZIP: {name}")
                with archive.extractfile(member) as stream:
                    verify(stream, member.size, expected[name], name)
                seen.add(name)
        if seen != set(expected):
            raise ValueError("Missing game samples: " + ", ".join(sorted(set(expected) - seen)))
    print(f"Verified {len(expected)} reviewed game sample Git blobs.")


if __name__ == "__main__":
    main()
