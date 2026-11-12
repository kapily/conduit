"""Audit the actual native payload before trusting a profile APK comparison."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path


def inspect(apk):
    entries = []
    with zipfile.ZipFile(apk) as archive:
        for entry in archive.infolist():
            with archive.open(entry) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            entries.append(
                {
                    "name": entry.filename,
                    "size": entry.file_size,
                    "compressedSize": entry.compress_size,
                    "sha256": digest,
                }
            )
    return {"size": apk.stat().st_size, "entries": entries}


def validate(manifest):
    entries = {entry["name"]: entry for entry in manifest["entries"]}
    assert len(entries) == len(manifest["entries"]), "Duplicate APK entries"
    for name in ("libapp.so", "libflutter.so"):
        assert entries.get("lib/x86_64/" + name, {}).get("size", 0) > 0, (
            "Missing x64 runtime: " + name
        )
    assert all(
        name.startswith("lib/x86_64/") for name in entries if name.startswith("lib/")
    ), "Unexpected native ABI in a split x64 APK"
    return entries


def validate_pair(baseline, candidate):
    a, b = validate(baseline), validate(candidate)
    assert a.keys() == b.keys(), "APK payload membership differs"
    assert (
        a["lib/x86_64/libflutter.so"]["sha256"]
        == b["lib/x86_64/libflutter.so"]["sha256"]
    ), "Flutter engines differ"
    assert a["lib/x86_64/libapp.so"]["sha256"] != b["lib/x86_64/libapp.so"]["sha256"], (
        "Changed app source produced identical AOT payloads"
    )


if __name__ == "__main__":
    manifest = inspect(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(manifest, indent=2) + "\n")
    if "--validate" in sys.argv[3:]:
        validate(manifest)
