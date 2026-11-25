"""Stage the two Journal Lab release downloads from a pinned Git commit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys


_ROOT = Path(__file__).resolve().parents[2]
_ASSETS = ("replay-starter.zip", "review-starter.zip")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _read_asset(commit: str, name: str) -> bytes:
    path = f"maintenance/journal/{name}"
    result = subprocess.run(
        ["git", "cat-file", "blob", f"{commit}:{path}"], cwd=_ROOT, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(f"cannot read {path} from {commit}")
    return result.stdout


def stage_assets(commit: str, output: Path) -> dict:
    """Read the two committed blobs and write them with their exact SHA256 values."""
    if not _COMMIT.fullmatch(commit):
        raise ValueError("use the full 40-character Git commit SHA")
    object_type = subprocess.run(["git", "cat-file", "-t", commit], cwd=_ROOT, capture_output=True, check=False)
    if object_type.returncode != 0 or object_type.stdout.strip() != b"commit":
        raise ValueError("the given SHA must identify a Git commit in this checkout")

    assets = {name: _read_asset(commit, name) for name in _ASSETS}
    checksums = {name: sha256(payload).hexdigest() for name, payload in assets.items()}
    checksums_name = "SHA256SUMS.txt"
    file_contents = {
        **assets,
        checksums_name: "".join(f"{digest}  {name}\n" for name, digest in checksums.items()).encode(),
    }
    output.mkdir()
    try:
        for name, payload in file_contents.items():
            (output / name).write_bytes(payload)
    except OSError:
        for name in file_contents:
            (output / name).unlink(missing_ok=True)
        output.rmdir()
        raise
    return {"commit": commit, "checksums": checksums, "output": str(output)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Full commit SHA containing both public sample ZIPs")
    parser.add_argument("--output", type=Path, required=True, help="New output directory with an existing parent")
    args = parser.parse_args(argv)
    try:
        staged = stage_assets(args.commit, args.output.expanduser())
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(staged, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
