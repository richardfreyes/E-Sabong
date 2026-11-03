"""Archive, verify and restore raw benchmark evidence kept outside Git.

Each run directory under ``docs/benchmarks/<date>/`` is packed whole into
``benchmark-archive/<date>/<run>.tar.zst`` (git-ignored). Bulky raw logs (``*.jsonl``) and the
redundant ``report.partial.json`` are then removed from the docs tree; reports, audits and
summaries stay in Git. Per-file SHA-256 lists of the *original* files are committed under
``docs/benchmarks/<date>/checksums/``, so a restore can be verified byte for byte.

    api/.venv/bin/python scripts/benchmarks/evidence.py archive --date 2026-11-02
    api/.venv/bin/python scripts/benchmarks/evidence.py verify  --date 2026-11-02
    api/.venv/bin/python scripts/benchmarks/evidence.py restore --date 2026-11-02 [--run retest]

Requires the ``zstd`` and ``tar`` command-line tools. Runs already in the manifest are skipped, so
new runs can be archived later. Never deletes a file before its archive has been written,
integrity-tested and checked to contain every original file.
"""

# ruff: noqa: S603, S607 -- fixed local tools (tar, zstd, shasum) on repository paths, no shell
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

if sys.version_info < (3, 12):  # noqa: UP036 - guards against running under the system python3
    sys.exit("Python 3.12+ required (use api/.venv/bin/python)")

ROOT = Path(os.environ.get("EVIDENCE_ROOT", Path(__file__).resolve().parents[2]))
PRUNE_SUFFIXES = (".jsonl",)
PRUNE_NAMES = ("report.partial.json",)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def paths(date: str) -> tuple[Path, Path, Path]:
    docs = ROOT / "docs" / "benchmarks" / date
    return docs, docs / "checksums", ROOT / "benchmark-archive" / date


def runs(docs: Path) -> list[Path]:
    return sorted(p for p in docs.iterdir() if p.is_dir() and p.name != "checksums")


def prunable(path: Path) -> bool:
    return path.suffix in PRUNE_SUFFIXES or path.name in PRUNE_NAMES


def archived_names(archive: Path) -> set[str]:
    with subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            names = {member.name for member in tar if member.isfile()}
    if proc.returncode:
        sys.exit(f"{archive}: decompression failed")
    return names


def pack(docs: Path, run: str, target: Path) -> None:
    """tar | zstd without a shell. No macOS metadata/xattr entries: exactly the original files."""
    env = {**os.environ, "COPYFILE_DISABLE": "1"}
    tar = subprocess.Popen(
        ["tar", "--no-mac-metadata", "--no-xattrs", "-cf", "-", "-C", str(docs), run], stdout=subprocess.PIPE, env=env
    )
    assert tar.stdout is not None
    zstd = subprocess.run(["zstd", "-19", "-T0", "-q", "-o", str(target)], stdin=tar.stdout, check=False)
    tar.stdout.close()
    if tar.wait() or zstd.returncode:
        target.unlink(missing_ok=True)
        sys.exit(f"{run}: packing failed; nothing was removed")


def archive(date: str) -> None:
    docs, checksums, store = paths(date)
    checksums.mkdir(exist_ok=True)
    store.mkdir(parents=True, exist_ok=True)
    existing = docs / "EVIDENCE-MANIFEST.json"
    manifest: dict = (
        json.loads(existing.read_text())
        if existing.exists()
        else {
            "date": date,
            "archive_dir": str(store.relative_to(ROOT)),
            "compression": "tar + zstd -19",
            "runs": {},
        }
    )
    for run in runs(docs):
        if run.name in manifest["runs"]:
            continue  # Already archived; its archive and checksums are never rewritten.
        files = sorted(p for p in run.rglob("*") if p.is_file())
        target = store / f"{run.name}.tar.zst"
        sums = checksums / f"{run.name}.sha256"
        if target.exists():
            sys.exit(f"{target} already exists; archives are never overwritten")
        if not any(prunable(f) for f in files):
            sys.exit(f"{run.name}: nothing to archive (already pruned?)")
        sums.write_text("".join(f"{sha256(f)}  {f.relative_to(docs)}\n" for f in files))
        pack(docs, run.name, target)
        subprocess.run(["zstd", "-t", "-q", str(target)], check=True)
        expected = {str(f.relative_to(docs)) for f in files}
        if archived_names(target) != expected:
            sys.exit(f"{target} does not contain exactly the original files; nothing was removed")
        removed = [f for f in files if prunable(f)]
        manifest["runs"][run.name] = {
            "archive": f"{run.name}.tar.zst",
            "archive_sha256": sha256(target),
            "archive_bytes": target.stat().st_size,
            "original_files": len(files),
            "original_bytes": sum(f.stat().st_size for f in files),
            "removed_from_git_tree": [f.name for f in removed],
            "removed_bytes": sum(f.stat().st_size for f in removed),
            "checksums": f"checksums/{run.name}.sha256",
        }
        for f in removed:
            f.unlink()
        print(
            json.dumps(
                {
                    "run": run.name,
                    **{k: v for k, v in manifest["runs"][run.name].items() if k != "removed_from_git_tree"},
                }
            )
        )
    (docs / "EVIDENCE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (store / "SHA256SUMS").write_text(
        "".join(f"{entry['archive_sha256']}  {entry['archive']}\n" for entry in manifest["runs"].values())
    )


def verify(date: str) -> None:
    docs, _, store = paths(date)
    manifest = json.loads((docs / "EVIDENCE-MANIFEST.json").read_text())
    failures = 0
    for name, entry in manifest["runs"].items():
        target = store / entry["archive"]
        ok = target.exists() and sha256(target) == entry["archive_sha256"]
        if ok:
            subprocess.run(["zstd", "-t", "-q", str(target)], check=True)
        failures += not ok
        print(f"{'OK  ' if ok else 'FAIL'} {name}: {target.relative_to(ROOT)}")
    sys.exit(1 if failures else 0)


def restore(date: str, only: str | None) -> None:
    docs, _, store = paths(date)
    manifest = json.loads((docs / "EVIDENCE-MANIFEST.json").read_text())
    for name, entry in manifest["runs"].items():
        if only and name != only:
            continue
        target = store / entry["archive"]
        if sha256(target) != entry["archive_sha256"]:
            sys.exit(f"{target}: checksum mismatch; not restoring")
        # Files still present in the docs tree (tracked reports/summaries) are left untouched;
        # the checksum pass below proves they are identical to the archived originals.
        with subprocess.Popen(["zstd", "-dc", str(target)], stdout=subprocess.PIPE) as proc:
            assert proc.stdout is not None
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                for member in tar:
                    if member.isfile() and not (docs / member.name).exists():
                        tar.extract(member, docs, filter="data")
        if proc.returncode:
            sys.exit(f"{target}: decompression failed")
        subprocess.run(
            ["shasum", "-a", "256", "-c", "--quiet", entry["checksums"]],
            cwd=docs,
            check=True,
        )
        print(f"restored and verified {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("archive", "verify", "restore"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--run")
    args = parser.parse_args()
    {
        "archive": lambda: archive(args.date),
        "verify": lambda: verify(args.date),
        "restore": lambda: restore(args.date, args.run),
    }[args.command]()


if __name__ == "__main__":
    main()
