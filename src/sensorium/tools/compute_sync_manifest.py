#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path


EXCLUDED_BASENAMES = {
    "Module.symvers",
    "modules.order",
    "compile_commands.json",
    ".DS_Store",
}
EXCLUDED_PATHS = {
    ".cache",
    "build",
    "dist",
    ".env.kernel",
    ".env.remote",
    "tools/libcamera-capture",
    "tools/libcamera-record",
    "tools/rgb24-to-rggb10",
}
EXCLUDED_SUFFIXES = (
    ".o",
    ".ko",
    ".mod",
    ".mod.c",
    ".cmd",
    ".pyc",
    ".swp",
    ".swo",
)


def is_excluded(rel_path: str) -> bool:
    rel_path = rel_path.strip("/")
    if not rel_path:
        return False
    parts = rel_path.split("/")
    if parts[0] in {".git", ".cache"}:
        return True
    if any(part == "__pycache__" for part in parts):
        return True
    if rel_path in EXCLUDED_PATHS:
        return True
    if any(part == ".tmp_versions" for part in parts):
        return True
    base = parts[-1]
    if base in EXCLUDED_BASENAMES:
        return True
    if base in {".env.kernel", ".env.remote"}:
        return True
    if base.startswith(".") and base.endswith(".cmd"):
        return True
    if base.startswith(".") and base.endswith(".d"):
        return True
    if base.endswith(EXCLUDED_SUFFIXES):
        return True
    return False


def git_output(repo_root: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo_root,
        stderr=subprocess.DEVNULL,
    )


def iter_git_paths(repo_root: Path, *args: str):
    data = git_output(repo_root, *args)
    if not data:
        return []
    return [item.decode("utf-8") for item in data.split(b"\0") if item]


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def update_entry(digest: hashlib._hashlib.HASH, prefix: str, rel_path: str, path: Path):
    st = path.lstat()
    mode = stat.S_IMODE(st.st_mode)
    digest.update(prefix.encode("utf-8"))
    digest.update(b"\0")
    digest.update(rel_path.encode("utf-8"))
    digest.update(b"\0")
    digest.update(f"{mode:o}".encode("ascii"))
    digest.update(b"\0")
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(os.readlink(path).encode("utf-8"))
        digest.update(b"\0")
        return
    if path.is_file():
        digest.update(b"file\0")
        digest.update(hash_file(path).encode("ascii"))
        digest.update(b"\0")


def git_manifest(repo_root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"sensorium-sync-manifest-v1\0")

    try:
        head = git_output(repo_root, "rev-parse", "--verify", "HEAD").decode("utf-8").strip()
    except subprocess.CalledProcessError:
        head = "NO_HEAD"
    digest.update(head.encode("utf-8"))
    digest.update(b"\0")

    changed_paths = set()
    for group in (
        iter_git_paths(repo_root, "ls-files", "-m", "-z"),
        iter_git_paths(repo_root, "ls-files", "-d", "-z"),
        iter_git_paths(repo_root, "ls-files", "--others", "--exclude-standard", "-z"),
    ):
        for rel_path in group:
            if not is_excluded(rel_path):
                changed_paths.add(rel_path)

    index_data = git_output(repo_root, "ls-files", "-s", "-z")
    for item in [part.decode("utf-8") for part in index_data.split(b"\0") if part]:
        meta, rel_path = item.split("\t", 1)
        if rel_path in changed_paths or is_excluded(rel_path):
            continue
        digest.update(b"index\0")
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(meta.encode("utf-8"))
        digest.update(b"\0")

    for rel_path in sorted(changed_paths):
        path = repo_root / rel_path
        if not path.exists() and not path.is_symlink():
            digest.update(b"deleted\0")
            digest.update(rel_path.encode("utf-8"))
            digest.update(b"\0")
            continue
        update_entry(digest, "worktree", rel_path, path)

    return digest.hexdigest()


def filesystem_manifest(repo_root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"sensorium-sync-manifest-v1-fallback\0")
    for path in sorted(repo_root.rglob("*")):
        if path.is_dir():
            continue
        rel_path = path.relative_to(repo_root).as_posix()
        if is_excluded(rel_path):
            continue
        update_entry(digest, "fs", rel_path, path)
    return digest.hexdigest()


def main() -> int:
    repo_root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    try:
        manifest = git_manifest(repo_root)
    except Exception:
        manifest = filesystem_manifest(repo_root)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
