#!/bin/sh
set -eu

# Match dependency_images.py's LF-normalized inputs on Windows and Linux.
kind="${1:?dependency kind required}"
case "$kind" in runtime|frontend) ;; *) exit 2 ;; esac
while read -r expected path; do
    if [ ! -f "$path" ]; then
        echo "Missing dependency input: $path" >&2
        exit 1
    fi
    actual="$(sed 's/\r$//' "$path" | sha256sum | cut -d ' ' -f 1)"
    if [ "$actual" != "$expected" ]; then
        echo "Stale $kind dependency image: $path changed. Run python3 tools/deploy/dependency_images.py ensure." >&2
        exit 1
    fi
done < "/opt/lanshare-dependencies/$kind.sha256"
