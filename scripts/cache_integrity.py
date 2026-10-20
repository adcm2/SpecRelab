#!/usr/bin/env python3
"""Check a sparse Git source cache without lazy fetching or changing it."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def blob_id(data):
    return subprocess.check_output(["git", "hash-object", "--stdin"], input=data, stderr=subprocess.PIPE).decode().strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("repo", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    os.environ.update(env)
    try:
        head = {}
        for record in git(repo, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, kind, oid = meta.split()
            head[path] = (mode, oid)
        index = {}
        for record in git(repo, "ls-files", "--stage", "-z").split(b"\0"):
            if not record:
                continue
            meta, path = record.split(b"\t", 1)
            mode, oid, stage = meta.split()
            index[path] = (mode, oid, stage)
        skip = set()
        for record in git(repo, "ls-files", "-t", "-z").split(b"\0"):
            if record and record[:1] == b"S":
                skip.add(record[2:])
        dirty = []
        for path in set(head) | set(index):
            expected = head.get(path)
            current = index.get(path)
            if current is None or current[2] != b"0" or expected != current[:2]:
                dirty.append(path.decode(errors="replace") + " (index)")
        for raw, (mode, oid, stage) in index.items():
            path = os.fsdecode(raw)
            full = repo / path
            if not full.exists() and not full.is_symlink():
                if raw not in skip:
                    dirty.append(path + " (missing tracked file)")
                continue
            if full.is_symlink():
                data = os.fsencode(os.readlink(full))
            elif full.is_file():
                data = full.read_bytes()
            else:
                continue
            if blob_id(data) != oid.decode():
                dirty.append(path + " (modified tracked file)")
        for directory, dirs, files in os.walk(repo, followlinks=False):
            dirs[:] = [name for name in dirs if name != ".git"]
            base = Path(directory)
            for name in files:
                full = base / name
                rel = os.fsencode(full.relative_to(repo).as_posix())
                if rel not in index:
                    dirty.append(os.fsdecode(rel) + " (untracked file)")
            for name in dirs:
                full = base / name
                if full.is_symlink():
                    rel = os.fsencode(full.relative_to(repo).as_posix())
                    if rel not in index:
                        dirty.append(os.fsdecode(rel) + " (untracked symlink)")
        if dirty:
            print(f"Refusing dirty {args.name} source cache at {repo}; preserve or clean its tracked/untracked changes before use.", file=sys.stderr)
            for path in dirty[:20]:
                print(f"  {path}", file=sys.stderr)
            return 1
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode(errors="replace") if exc.stderr else str(exc)
        print(f"Cannot verify {args.name} source cache offline without fetching objects: {message.strip()}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
