#!/usr/bin/env python3
"""Build and verify the dataset release ZIP using only committed public files."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v1.0.0")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.version):
        parser.error("Version must have the form v1.0.0.")
    selected = ["SynResLoadPattern", "SynResLoadPatternClustered", "README.md", "LICENSE.txt",
                "dataset.ipynb", "cluster_overview.png", "cluster_profile_distribution.png"]
    if git("status", "--porcelain", "--", *selected):
        raise RuntimeError("Commit the release files before packaging.")
    names = git("ls-files", "--", *selected).splitlines()
    if not names or not any(name.endswith(".csv") for name in names):
        raise RuntimeError("Dataset files are missing.")
    commit = git("rev-parse", "HEAD")
    manifest = {
        "version": args.version, "repository": "https://github.com/AdamLiang42/SynResLoadPattern",
        "source_commit": commit, "files": [
            {"path": name, "bytes": (ROOT / name).stat().st_size, "sha256": sha256(ROOT / name)}
            for name in names],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / "SynResLoadPattern.zip"
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite {target}")
    prefix = f"SynResLoadPattern-{args.version}/"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in names:
            archive.write(ROOT / name, arcname=prefix + name)
        archive.writestr(prefix + "MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
    # Verify every decompressed byte against the manifest before an asset can be uploaded.
    with zipfile.ZipFile(target) as archive:
        for entry in manifest["files"]:
            digest = hashlib.sha256()
            with archive.open(prefix + entry["path"]) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise RuntimeError(f"ZIP integrity check failed for {entry['path']}")
    size = target.stat().st_size
    if size >= 2 * 1024 ** 3:
        raise RuntimeError("ZIP exceeds the GitHub release asset limit.")
    checksum = sha256(target)
    (args.output_dir / "SHA256SUMS.txt").write_text(f"{checksum}  {target.name}\n")
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"asset": str(target.resolve()), "bytes": size,
                      "sha256": checksum, "verified_files": len(names), "source_commit": commit}, indent=2))


if __name__ == "__main__":
    main()
