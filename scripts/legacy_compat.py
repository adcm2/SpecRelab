#!/usr/bin/env python3
"""Build and verify the frozen old-origin compatibility artifact."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from html.parser import HTMLParser
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "compatibility-map.json"
EXPECTED_GPL_COMMIT = "4ef3a66c62d64408d99989dd51c3ccbdc46d0b0c"
MODEL_PREFIX = "/gplspec/modeldata/"


class CompatibilityError(RuntimeError):
    pass


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("name"):
            self.ids.add(values["name"])


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not name.startswith("/")
        and "\\" not in name
        and all(part not in ("", ".", "..") for part in path.parts)
        and path.parts[0] in {"DSpecM1D", "gplspec"}
    )


def route_records(mapping: dict) -> list[dict]:
    return mapping["dspecm1d_routes"] + mapping["gplspec_routes"]


def expected_archive_members(mapping: dict) -> dict[str, tuple[str, int]]:
    expected: dict[str, tuple[str, int]] = {}
    for record in route_records(mapping):
        member = record.get("archive_member")
        if not member:
            continue
        if not safe_member(member):
            raise CompatibilityError(f"unsafe archive path in compatibility map: {member!r}")
        sha = record.get("sha256")
        size = record.get("bytes")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise CompatibilityError(f"missing or invalid body SHA-256 for {member}")
        if not isinstance(size, int) or size < 0:
            raise CompatibilityError(f"missing or invalid body size for {member}")
        previous = expected.setdefault(member, (sha, size))
        if previous != (sha, size):
            raise CompatibilityError(f"conflicting map bodies for {member}")
    if not expected:
        raise CompatibilityError("compatibility map has no archived static members")
    return expected


def validate_archive(map_path: Path = MAP_PATH) -> tuple[dict, dict[str, bytes]]:
    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    descriptor = mapping.get("legacy_archive", {})
    rel = descriptor.get("path", "")
    archive_path = (map_path.parent / rel).resolve()
    try:
        archive_path.relative_to(map_path.parent.resolve())
    except ValueError as exc:
        raise CompatibilityError("archive descriptor escapes the website root") from exc
    if not archive_path.is_file():
        raise CompatibilityError(f"legacy archive missing: {archive_path}")
    archive_bytes = archive_path.read_bytes()
    if digest(archive_bytes) != descriptor.get("sha256"):
        raise CompatibilityError("legacy archive hash does not match compatibility-map.json")
    expected = expected_archive_members(mapping)
    found: dict[str, bytes] = {}
    expanded = 0
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for info in archive.getmembers():
                if not safe_member(info.name):
                    raise CompatibilityError(f"unsafe archive member: {info.name!r}")
                if not info.isfile() or info.issym() or info.islnk():
                    raise CompatibilityError(f"archive member is not a regular file: {info.name}")
                if info.name in found:
                    raise CompatibilityError(f"duplicate archive member: {info.name}")
                stream = archive.extractfile(info)
                if stream is None:
                    raise CompatibilityError(f"cannot read archive member: {info.name}")
                body = stream.read()
                if len(body) != info.size:
                    raise CompatibilityError(f"truncated archive member: {info.name}")
                found[info.name] = body
                expanded += len(body)
    except (tarfile.TarError, OSError) as exc:
        raise CompatibilityError(f"cannot read legacy archive: {exc}") from exc
    if set(found) != set(expected):
        missing = sorted(set(expected) - set(found))[:5]
        extra = sorted(set(found) - set(expected))[:5]
        raise CompatibilityError(f"archive membership mismatch; missing={missing}, extra={extra}")
    for name, body in found.items():
        sha, size = expected[name]
        if len(body) != size or digest(body) != sha:
            raise CompatibilityError(f"archived body mismatch: {name}")
    if len(found) != descriptor.get("files") or expanded != descriptor.get("expanded_bytes"):
        raise CompatibilityError("archive count or expanded size differs from its descriptor")
    return mapping, found


def mapped_manual(record: dict) -> bool:
    path = record.get("legacy_path", "")
    action = record.get("action", "")
    return (
        bool(record.get("archive_member", "").lower().endswith((".html", ".htm")))
        and bool(record.get("new"))
        and not path.startswith("/DSpecM1D/api/")
        and "modeldata/" not in path
        and "retain-" not in action
    )


def canonical_target(record: dict) -> str:
    target = record["new"]
    if not target.startswith("/SpecRelab/"):
        raise CompatibilityError(f"manual target is not under the SpecRelab prefix: {target}")
    return "https://adcm2.github.io" + target


def banner_markup(target: str) -> str:
    safe = html.escape(target, quote=True)
    return (
        '<aside class="legacy-migration-banner" role="note">'
        '<style>.legacy-migration-banner{box-sizing:border-box;margin:0;padding:.8rem 1rem;'
        'border-bottom:3px solid #d9a928;background:#fff7df;color:#18263b;font:600 1rem/1.5 sans-serif}'
        '.legacy-migration-banner a{color:#123d70;text-decoration:underline;font-weight:700}</style>'
        'This archived page is retained for compatibility. '
        f'<a href="{safe}">Open the current SpecRelab documentation</a>.'
        '</aside>'
    )


def overlay_html(body: bytes, record: dict, anchor_sets: dict[str, list[str]]) -> bytes:
    target = canonical_target(record)
    if target not in anchor_sets:
        raise CompatibilityError(f"manual target has no frozen anchor set: {target}")
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<script\b[^>]*src=[\"'][^\"']*polyfill\.io[^\"']*[\"'][^>]*>\s*</script\s*>", "", text)
    anchors = anchor_sets[target]
    script_data = json.dumps(anchors, ensure_ascii=True).replace("<", "\\u003c")
    script = (
        '<script>(function(){"use strict";var target='
        + json.dumps(target)
        + ",anchors=new Set(" + script_data + ");"
        + "if(location.hash){var fragment;try{fragment=decodeURIComponent(location.hash.slice(1));}"
        + "catch(error){return;}if(!anchors.has(fragment)){return;}}"
        + "var next=new URL(target);next.search=location.search;"
        + "if(location.hash){next.hash=location.hash;}location.replace(next.href);})();</script>"
    )
    canonical = f'<link rel="canonical" href="{html.escape(target, quote=True)}">'
    notice = banner_markup(target)
    if re.search(r"(?is)<head\b[^>]*>", text):
        text = re.sub(r"(?is)(<head\b[^>]*>)", r"\1" + canonical, text, count=1)
    else:
        text = canonical + text
    if re.search(r"(?is)<body\b[^>]*>", text):
        text = re.sub(r"(?is)(<body\b[^>]*>)", r"\1" + notice, text, count=1)
    else:
        text = notice + text
    if re.search(r"(?is)</body\s*>", text):
        text = re.sub(r"(?is)</body\s*>", script + "</body>", text, count=1)
    else:
        text += script
    return text.encode("utf-8")


def archived_output_body(member: str, body: bytes, record: dict, mapping: dict) -> bytes:
    if mapped_manual(record):
        return overlay_html(body, record, mapping["verified_target_anchor_sets"])
    if member.lower().endswith((".html", ".htm")) and not (member.startswith("DSpecM1D/api/") or "/api/" in record.get("legacy_path", "")):
        return re.sub(rb"(?is)<script\b[^>]*src=[\"'][^\"']*polyfill\.io[^\"']*[\"'][^>]*>\s*</script\s*>", b"", body)
    return body


def current_anchor_ids(site: Path, route: str, baseurl: str) -> set[str]:
    path = route.split("#", 1)[0].split("?", 1)[0]
    if baseurl and (path == baseurl or path.startswith(baseurl + "/")):
        path = path[len(baseurl):] or "/"
    relative = path.lstrip("/")
    page = site / relative
    if page.is_dir() or not page.suffix:
        page = page / "index.html"
    if not page.is_file():
        raise CompatibilityError(f"verified current target is missing: {page}")
    parser = AnchorParser()
    parser.feed(page.read_text(encoding="utf-8", errors="replace"))
    return parser.ids


def validate_target_anchor_metadata(mapping: dict) -> set[str]:
    anchors = mapping.get("verified_target_anchor_sets", {})
    needed = {canonical_target(row) for row in route_records(mapping) if mapped_manual(row)}
    if not needed.issubset(anchors):
        missing = sorted(needed - set(anchors))
        raise CompatibilityError(f"manual targets lack frozen anchor metadata: {missing[:5]}")
    alias = mapping.get("quick_start_alias", {})
    installation = "https://adcm2.github.io/SpecRelab/dspecm1d/installation.html"
    if alias.get("legacy", "").endswith("#quick-start") and "quick-start" not in anchors.get(installation, []):
        raise CompatibilityError("the documented #quick-start alias is absent from frozen target anchors")
    return needed


def validate_target_anchors(mapping: dict, root_site: Path, prefix_site: Path) -> None:
    needed = validate_target_anchor_metadata(mapping)
    anchors = mapping["verified_target_anchor_sets"]
    for target in sorted(needed):
        route = target.removeprefix("https://adcm2.github.io")
        expected = set(anchors[target])
        root_ids = current_anchor_ids(root_site, route, "/SpecRelab")
        prefix_ids = current_anchor_ids(prefix_site, route, "/SpecRelab")
        if root_ids != expected or prefix_ids != expected:
            raise CompatibilityError(f"frozen target anchor mismatch for {route}")


def verify_protected_api_body(member: str, actual: bytes, archived: bytes) -> None:
    if member.startswith("DSpecM1D/api/") and actual != archived:
        raise CompatibilityError(f"protected DSpecM1D API body changed: {member}")


def modeldata_records(mapping: dict) -> list[dict]:
    return [row for row in mapping["gplspec_routes"] if MODEL_PREFIX in row.get("legacy_path", "")]


def read_blob(repo: Path, blob: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "blob", blob],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        raise CompatibilityError(f"cannot read immutable GPLSpec blob {blob}: {exc.stderr.decode(errors='replace').strip()}") from exc
    return result.stdout


def verify_modeldata_tree(mapping: dict, tree_entries: dict[str, tuple[str, int]]) -> None:
    expected = {}
    for row in modeldata_records(mapping):
        rel = row["legacy_path"].removeprefix(MODEL_PREFIX)
        if not safe_member("gplspec/modeldata/" + rel):
            raise CompatibilityError(f"unsafe modeldata route: {row['legacy_path']}")
        expected["modeldata/" + rel] = (row.get("source_blob"), row.get("bytes"))
    if tree_entries != expected:
        missing = sorted(set(expected) - set(tree_entries))[:5]
        extra = sorted(set(tree_entries) - set(expected))[:5]
        raise CompatibilityError(f"modeldata map differs from pinned Git tree; missing={missing}, extra={extra}")
    for path, (blob, size) in expected.items():
        if tree_entries[path] != (blob, size):
            raise CompatibilityError(f"modeldata Git blob or size differs from pinned tree: {path}")


def modeldata_tree(repo: Path, commit: str) -> dict[str, tuple[str, int]]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-tree", "-r", "-l", commit, "--", "modeldata"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise CompatibilityError(f"cannot read pinned GPL modeldata tree: {exc.stderr.strip()}") from exc
    entries = {}
    for line in result.stdout.splitlines():
        metadata, path = line.split("\t", 1)
        mode, kind, blob, size = metadata.split()
        if kind != "blob" or not size.isdigit():
            raise CompatibilityError(f"unexpected object in pinned modeldata tree: {path}")
        entries[path] = (blob, int(size))
    return entries


def build(output: Path, scope: str, gpl_repo: Path | None, gpl_commit: str) -> None:
    mapping, bodies = validate_archive()
    stage_root = (ROOT / ".build/stage4").resolve()
    output = output.resolve()
    try:
        output.relative_to(stage_root)
    except ValueError as exc:
        raise CompatibilityError("legacy output must stay inside ignored .build/stage4") from exc
    if output == stage_root:
        raise CompatibilityError("legacy output cannot replace the Stage4 evidence directory")
    if scope in ("both", "gplspec"):
        if gpl_commit != EXPECTED_GPL_COMMIT:
            raise CompatibilityError(f"GPL modeldata source commit must be {EXPECTED_GPL_COMMIT}")
        if gpl_repo is None:
            raise CompatibilityError("--gpl-repo is required when building GPLSpec")
        got = subprocess.run(["git", "-C", str(gpl_repo), "cat-file", "-e", gpl_commit + "^{commit}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if got.returncode:
            raise CompatibilityError(f"GPL data source lacks immutable commit {gpl_commit}")
        verify_modeldata_tree(mapping, modeldata_tree(gpl_repo, gpl_commit))
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    allowed = {"both": ("DSpecM1D", "gplspec"), "dspecm1d": ("DSpecM1D",), "gplspec": ("gplspec",)}[scope]
    member_to_row = {}
    for row in route_records(mapping):
        if row.get("archive_member"):
            member_to_row.setdefault(row["archive_member"], row)
    for member, body in bodies.items():
        section = PurePosixPath(member).parts[0]
        if section not in allowed:
            continue
        row = member_to_row[member]
        relative = PurePosixPath(member).relative_to(section) if scope != "both" else PurePosixPath(member)
        dest = output.joinpath(*relative.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = archived_output_body(member, body, row, mapping)
        dest.write_bytes(body)
    if scope in ("both", "gplspec"):
        for row in modeldata_records(mapping):
            blob = row.get("source_blob")
            expected_size = row.get("bytes")
            if not isinstance(blob, str) or not re.fullmatch(r"[0-9a-f]{40}", blob):
                raise CompatibilityError(f"invalid modeldata Git blob for {row['legacy_path']}")
            body = read_blob(gpl_repo, blob)
            if len(body) != expected_size:
                raise CompatibilityError(f"modeldata size mismatch for {row['legacy_path']}: {len(body)} != {expected_size}")
            rel = row["legacy_path"].removeprefix(MODEL_PREFIX)
            if not safe_member("gplspec/modeldata/" + rel):
                raise CompatibilityError(f"unsafe modeldata route: {row['legacy_path']}")
            data_base = output / "gplspec" if scope == "both" else output
            dest = data_base / "modeldata" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
    print(f"Built {scope} legacy compatibility output: {output}")


def check(output: Path, scope: str, root_site: Path | None, prefix_site: Path | None, gpl_repo: Path | None, gpl_commit: str) -> None:
    mapping, bodies = validate_archive()
    validate_target_anchor_metadata(mapping)
    if root_site is not None or prefix_site is not None:
        if root_site is None or prefix_site is None:
            raise CompatibilityError("provide both --root-site and --prefix-site for current-target validation")
        validate_target_anchors(mapping, root_site.resolve(), prefix_site.resolve())
    output = output.resolve()
    if not output.is_dir():
        raise CompatibilityError(f"legacy output does not exist: {output}")
    allowed = {"both": ("DSpecM1D", "gplspec"), "dspecm1d": ("DSpecM1D",), "gplspec": ("gplspec",)}[scope]
    records = {}
    for record in route_records(mapping):
        if record.get("archive_member"):
            records.setdefault(record["archive_member"], record)
    expected_paths = set()
    for member, original in bodies.items():
        section = PurePosixPath(member).parts[0]
        if section not in allowed:
            continue
        row = records[member]
        rel = PurePosixPath(member).relative_to(section) if scope != "both" else PurePosixPath(member)
        path = output.joinpath(*rel.parts)
        if not path.is_file():
            raise CompatibilityError(f"legacy artifact file missing: {path}")
        expected_paths.add(path.relative_to(output).as_posix())
        actual = path.read_bytes()
        verify_protected_api_body(member, actual, original)
        expected_body = archived_output_body(member, original, row, mapping)
        if actual != expected_body:
            raise CompatibilityError(f"legacy static body differs from its approved archived transformation: {member}")
    for path in output.rglob("*"):
        if path.is_file() and path.relative_to(output).as_posix() not in expected_paths and not path.relative_to(output).as_posix().startswith(("modeldata/", "gplspec/modeldata/")):
            raise CompatibilityError(f"unexpected compatibility output file: {path.relative_to(output)}")
    if scope in ("both", "gplspec"):
        if gpl_repo is None:
            raise CompatibilityError("--gpl-repo is required to verify GPLSpec modeldata")
        if gpl_commit != EXPECTED_GPL_COMMIT:
            raise CompatibilityError("GPLSpec source commit pin changed")
        verify_modeldata_tree(mapping, modeldata_tree(gpl_repo, gpl_commit))
        for row in modeldata_records(mapping):
            rel = row["legacy_path"].removeprefix(MODEL_PREFIX)
            data_base = output / "gplspec" if scope == "both" else output
            path = data_base / "modeldata" / rel
            body = path.read_bytes() if path.is_file() else b""
            if len(body) != row.get("bytes") or body != read_blob(gpl_repo, row["source_blob"]):
                raise CompatibilityError(f"GPL modeldata body mismatch: {row['legacy_path']}")
    checked_manual = set()
    for row in route_records(mapping):
        if not mapped_manual(row):
            continue
        member = row["archive_member"]
        section = PurePosixPath(member).parts[0]
        if section not in allowed or member in checked_manual:
            continue
        checked_manual.add(member)
        rel = PurePosixPath(member).relative_to(section) if scope != "both" else PurePosixPath(member)
        text = (output.joinpath(*rel.parts)).read_text(encoding="utf-8", errors="replace")
        canonical = re.search(r'<link rel="canonical" href="([^"]+)">', text)
        accepted = {canonical_target(item) for item in route_records(mapping) if item.get("archive_member") == member and mapped_manual(item)}
        if not canonical or canonical.group(1) not in accepted:
            raise CompatibilityError(f"canonical target missing or invalid in {member}")
        if 'Open the current SpecRelab documentation' not in text or 'location.replace(next.href)' not in text:
            raise CompatibilityError(f"compatibility link or JavaScript redirect missing from {member}")
        if 'next.search=location.search' not in text or 'if(!anchors.has(fragment)){return;}' not in text:
            raise CompatibilityError(f"query or unknown-fragment fallback missing from {member}")
        if 'polyfill.io' in text:
            raise CompatibilityError(f"obsolete polyfill remains in archived manual page {member}")
    print(f"PASS: {len(bodies)} archive bodies, DSpec API bytes, mapped manual redirects, frozen anchors, and modeldata identity ({scope})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "check"):
        sub = commands.add_parser(name)
        sub.add_argument("--scope", choices=("both", "dspecm1d", "gplspec"), default="both")
        sub.add_argument("--output", type=Path, required=True)
        sub.add_argument("--gpl-repo", type=Path)
        sub.add_argument("--gpl-commit", default=EXPECTED_GPL_COMMIT)
        if name == "check":
            sub.add_argument("--root-site", type=Path)
            sub.add_argument("--prefix-site", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            build(args.output, args.scope, args.gpl_repo, args.gpl_commit)
        else:
            check(args.output, args.scope, args.root_site, args.prefix_site, args.gpl_repo, args.gpl_commit)
    except (CompatibilityError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
