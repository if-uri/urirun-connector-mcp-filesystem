# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

"""Sandboxed filesystem routes for urirun, in the spirit of MCP filesystem servers.

Each route is declared once with a typed ``@conn.handler``: the function signature
becomes the input schema and the body is the implementation — no argv template, no
``_exec.py``, no ``run_route`` dispatcher. ``isolated=True`` runs each route
out-of-process through the shared ``python -m urirun.exec`` runner, so the binding
stays **registry-portable**: it executes from a compiled/served registry
(``urirun run`` / ``urirun node serve``) with only the package importable — no
console-script install and no per-connector shim.

Every route is sandboxed under a single root directory (``IFURI_FS_ROOT`` or the
current working directory). Paths that resolve outside the root are rejected, so
``fs://`` flows cannot read or move arbitrary host files. Query routes are
read-only; mutating command routes default to dry-run.

* ``fs://host/dir/query/list``   -- list a directory
* ``fs://host/file/query/read``  -- read a text file (size-capped)
* ``fs://host/path/query/stat``  -- stat a path
* ``fs://host/dir/command/prune_empty`` -- remove empty directories
* ``fs://host/file/command/write_text`` -- write a UTF-8 text file
* ``fs://host/file/command/write_blob`` -- write base64-decoded binary bytes
* ``fs://host/file/command/move_to_dir`` -- move one file into a directory
* ``fs://host/file/command/move`` -- move/rename one file to a target path

The manifest stays prose-only; ``routes``/``uriSchemes`` are derived from the
declared routes.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any

import urirun

CONNECTOR_ID = "mcp-filesystem"
conn = urirun.connector(CONNECTOR_ID, scheme="fs")

DEFAULT_MAX_BYTES = 65536
DEFAULT_MAX_DUPLICATE_FILES = 10000
DEFAULT_MAX_BLOB_BYTES = 10 * 1024 * 1024


# --- sandbox helpers (real implementation) --------------------------------

def root() -> Path:
    """Sandbox root; everything the connector reads must live under it."""
    return Path(os.environ.get("IFURI_FS_ROOT", ".")).expanduser().resolve()


def _resolve(path: str) -> Path | None:
    """Resolve ``path`` under the sandbox root, or None if it escapes the root."""
    base = root()
    raw = Path(path).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
    if candidate == base or base in candidate.parents:
        return candidate
    return None


def _error(path: str, message: str) -> dict[str, Any]:
    return {"ok": False, "connector": CONNECTOR_ID, "path": path, "error": message}


def _split_words(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.replace(";", ",").split(",") if item.strip()]


def _is_hidden(path: Path, base: Path) -> bool:
    try:
        relative = path.relative_to(base)
    except ValueError:
        return False
    return any(part.startswith(".") for part in relative.parts)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, base: Path) -> str:
    return str(path.relative_to(base))


# --- route handlers: schema + implementation all derived ------------------

@conn.handler("dir/query/list", isolated=True, meta={"label": "List a directory"})
def list_dir(path: str = ".") -> dict[str, Any]:
    """List a directory under the sandbox root."""
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    if not target.exists():
        return _error(path, "path does not exist")
    if not target.is_dir():
        return _error(path, "path is not a directory")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: item.name):
        kind = "dir" if child.is_dir() else "file" if child.is_file() else "other"
        entries.append({
            "name": child.name,
            "type": kind,
            "size": child.stat().st_size if child.is_file() else None,
        })
    return {"ok": True, "connector": CONNECTOR_ID, "path": path, "root": str(root()), "entries": entries}


@conn.handler("dir/command/prune_empty", isolated=True, external=True,
              meta={"label": "Prune empty directories"})
def prune_empty_dirs(
    path: str = ".",
    dry_run: bool = True,
    exclude: str = "no_invoice",
    max_dirs: int = 10000,
) -> dict[str, Any]:
    """Remove empty directories under ``path`` without leaving the sandbox root."""
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    if not target.exists():
        return _error(path, "path does not exist")
    if not target.is_dir():
        return _error(path, "path is not a directory")

    base = root()
    excluded = set(_split_words(exclude))
    all_dirs = []
    for child in target.rglob("*"):
        if len(all_dirs) >= max_dirs:
            break
        if not child.is_dir():
            continue
        rel = _relative(child, base)
        rel_parts = set(Path(rel).parts)
        if excluded and (rel in excluded or rel_parts.intersection(excluded)):
            continue
        all_dirs.append(child)
    all_dirs.sort(key=lambda item: len(item.parts), reverse=True)

    planned: set[Path] = set()
    candidates: list[Path] = []
    for directory in all_dirs:
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        if all(child in planned for child in children):
            planned.add(directory)
            candidates.append(directory)

    removed: list[str] = []
    errors: list[dict[str, str]] = []
    if dry_run:
        removed = [_relative(directory, base) for directory in candidates]
    else:
        for directory in candidates:
            rel = _relative(directory, base)
            try:
                directory.rmdir()
                removed.append(rel)
            except OSError as exc:
                errors.append({"path": rel, "error": str(exc)})

    return {
        "ok": not errors,
        "connector": CONNECTOR_ID,
        "path": path,
        "root": str(base),
        "dryRun": dry_run,
        "removedCount": len(removed),
        "errorCount": len(errors),
        "truncated": len(all_dirs) >= max_dirs,
        "removed": removed,
        "errors": errors,
    }


@conn.handler("file/query/read", isolated=True, meta={"label": "Read a text file"})
def read_file(path: str = "", max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """Read a text file under the sandbox root (size-capped)."""
    if not path:
        return _error(path, "path is required")
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    if not target.exists():
        return _error(path, "path does not exist")
    if not target.is_file():
        return _error(path, "path is not a file")
    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    chunk = raw[:max_bytes]
    try:
        content = chunk.decode("utf-8")
    except UnicodeDecodeError:
        return _error(path, "file is not valid UTF-8 text")
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "content": content,
        "truncated": truncated,
        "bytes": len(raw),
    }


@conn.handler("file/query/blob", isolated=True, meta={"label": "Read a binary file as base64"})
def read_blob(path: str = "", max_bytes: int = DEFAULT_MAX_BLOB_BYTES) -> dict[str, Any]:
    """Read a binary file under the sandbox root as base64, size-capped."""
    if not path:
        return _error(path, "path is required")
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    if not target.exists():
        return _error(path, "path does not exist")
    if not target.is_file():
        return _error(path, "path is not a file")
    size = target.stat().st_size
    if size > max_bytes:
        return _error(path, f"file exceeds max_bytes ({size} > {max_bytes})")
    raw = target.read_bytes()
    mime, _encoding = mimetypes.guess_type(str(target))
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "name": target.name,
        "mime": mime or "application/octet-stream",
        "size": size,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes_b64": base64.b64encode(raw).decode("ascii"),
    }


@conn.handler("file/command/write_text", isolated=True, external=True,
              meta={"label": "Write a UTF-8 text file"})
def write_text_file(
    path: str = "",
    content: str = "",
    dry_run: bool = True,
    overwrite: bool = False,
    make_parents: bool = True,
    mode: str = "",
) -> dict[str, Any]:
    """Write a UTF-8 text file under the sandbox root."""
    if not path:
        return _error(path, "path is required")
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    base = root()
    try:
        relative = target.relative_to(base)
    except ValueError:
        return _error(path, "path escapes the sandbox root")
    if target.exists():
        if target.is_dir():
            return _error(str(relative), "destination is a directory")
        if not overwrite:
            return _error(str(relative), "destination exists")
    if not make_parents and not target.parent.exists():
        return _error(str(relative), "destination parent does not exist")

    parsed_mode: int | None = None
    if mode:
        try:
            parsed_mode = int(mode, 8)
        except ValueError:
            return _error(str(relative), "mode must be an octal string, for example 0644")

    raw = content.encode("utf-8")
    if not dry_run:
        if make_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        if parsed_mode is not None:
            os.chmod(target, parsed_mode)

    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "target": str(relative),
        "dryRun": dry_run,
        "written": not dry_run,
        "bytes": len(raw),
        "mode": mode,
    }


@conn.handler("file/command/write_blob", isolated=True, external=True,
              meta={"label": "Write a binary file from base64"})
def write_blob_file(
    path: str = "",
    bytes_b64: str = "",
    dry_run: bool = True,
    overwrite: bool = False,
    make_parents: bool = True,
    mode: str = "",
) -> dict[str, Any]:
    """Write base64-decoded bytes under the sandbox root."""
    if not path:
        return _error(path, "path is required")
    if not bytes_b64:
        return _error(path, "bytes_b64 is required")
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    base = root()
    try:
        relative = target.relative_to(base)
    except ValueError:
        return _error(path, "path escapes the sandbox root")
    if target.exists():
        if target.is_dir():
            return _error(str(relative), "destination is a directory")
        if not overwrite:
            return _error(str(relative), "destination exists")
    if not make_parents and not target.parent.exists():
        return _error(str(relative), "destination parent does not exist")

    parsed_mode: int | None = None
    if mode:
        try:
            parsed_mode = int(mode, 8)
        except ValueError:
            return _error(str(relative), "mode must be an octal string, for example 0644")
    try:
        raw = base64.b64decode(bytes_b64.encode("ascii"), validate=True)
    except Exception:
        return _error(str(relative), "bytes_b64 must be valid base64")

    if not dry_run:
        if make_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        if parsed_mode is not None:
            os.chmod(target, parsed_mode)
    mime, _encoding = mimetypes.guess_type(str(target))
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "target": str(relative),
        "dryRun": dry_run,
        "written": not dry_run,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mime": mime or "application/octet-stream",
        "mode": mode,
    }


@conn.handler("path/query/stat", isolated=True, meta={"label": "Stat a path"})
def stat_path(path: str = "") -> dict[str, Any]:
    """Stat a path under the sandbox root."""
    if not path:
        return _error(path, "path is required")
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    if not target.exists():
        return _error(path, "path does not exist")
    info = target.stat()
    kind = "dir" if target.is_dir() else "file" if target.is_file() else "other"
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "type": kind,
        "size": info.st_size,
        "modifiedEpoch": int(info.st_mtime),
    }


@conn.handler("duplicates/query/find", isolated=True, meta={"label": "Find duplicate files"})
def find_duplicates(
    path: str = ".",
    recursive: bool = True,
    min_size: int = 1,
    max_files: int = DEFAULT_MAX_DUPLICATE_FILES,
    include_hidden: bool = False,
    extensions: str = "",
) -> dict[str, Any]:
    """Find exact duplicate files under the sandbox root using SHA-256."""
    target = _resolve(path)
    if target is None:
        return _error(path, "path escapes the sandbox root")
    if not target.exists():
        return _error(path, "path does not exist")
    if not target.is_dir():
        return _error(path, "path is not a directory")

    base = root()
    allowed_exts = set(_split_words(extensions))
    if allowed_exts:
        allowed_exts = {ext if ext.startswith(".") else f".{ext}" for ext in allowed_exts}

    size_buckets: dict[int, list[Path]] = {}
    scanned = 0
    skipped = 0
    iterator = target.rglob("*") if recursive else target.glob("*")
    for child in iterator:
        if max_files and scanned >= max_files:
            break
        if not child.is_file():
            continue
        if not include_hidden and _is_hidden(child, base):
            skipped += 1
            continue
        if allowed_exts and child.suffix.lower() not in allowed_exts:
            skipped += 1
            continue
        try:
            size = child.stat().st_size
        except OSError:
            skipped += 1
            continue
        if size < min_size:
            skipped += 1
            continue
        scanned += 1
        size_buckets.setdefault(size, []).append(child)

    groups: list[dict[str, Any]] = []
    hashed = 0
    for size, files in sorted(size_buckets.items()):
        if len(files) < 2:
            continue
        hash_buckets: dict[str, list[Path]] = {}
        for file_path in files:
            try:
                digest = _file_hash(file_path)
            except OSError:
                skipped += 1
                continue
            hashed += 1
            hash_buckets.setdefault(digest, []).append(file_path)
        for digest, dupes in sorted(hash_buckets.items()):
            if len(dupes) < 2:
                continue
            rel_paths = [str(file_path.relative_to(base)) for file_path in sorted(dupes)]
            groups.append({
                "sha256": digest,
                "size": size,
                "count": len(rel_paths),
                "paths": rel_paths,
                "reclaimableBytes": size * (len(rel_paths) - 1),
            })

    duplicate_files = sum(group["count"] for group in groups)
    reclaimable = sum(group["reclaimableBytes"] for group in groups)
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "root": str(base),
        "recursive": recursive,
        "scannedFiles": scanned,
        "hashedFiles": hashed,
        "skippedFiles": skipped,
        "groupCount": len(groups),
        "duplicateFiles": duplicate_files,
        "reclaimableBytes": reclaimable,
        "truncated": bool(max_files and scanned >= max_files),
        "groups": groups,
    }


@conn.handler("file/command/move_to_dir", isolated=True, external=True,
              meta={"label": "Move one file into a sandboxed directory"})
def move_to_dir(
    path: str = "",
    target_dir: str = "no_invoice",
    preserve_relative: bool = True,
    dry_run: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Move one file under the sandbox root into another sandboxed directory."""
    if not path:
        return _error(path, "path is required")
    source = _resolve(path)
    if source is None:
        return _error(path, "path escapes the sandbox root")
    if not source.exists():
        return _error(path, "path does not exist")
    if not source.is_file():
        return _error(path, "path is not a file")

    base = root()
    target_base = _resolve(target_dir)
    if target_base is None:
        return _error(target_dir, "target_dir escapes the sandbox root")

    try:
        relative = source.relative_to(base)
    except ValueError:
        return _error(path, "path escapes the sandbox root")
    dest = target_base / relative if preserve_relative else target_base / source.name
    dest = dest.resolve()
    if not (dest == base or base in dest.parents):
        return _error(str(dest), "destination escapes the sandbox root")
    if source == dest:
        return {
            "ok": True,
            "connector": CONNECTOR_ID,
            "path": path,
            "target_dir": target_dir,
            "dryRun": dry_run,
            "moved": False,
            "reason": "already at destination",
            "source": str(relative),
            "target": str(dest.relative_to(base)),
        }
    if target_base in source.parents or source == target_base:
        return {
            "ok": True,
            "connector": CONNECTOR_ID,
            "path": path,
            "target_dir": target_dir,
            "dryRun": dry_run,
            "moved": False,
            "reason": "already under target_dir",
            "source": str(relative),
            "target": str(relative),
        }
    if dest.exists() and not overwrite:
        return _error(str(dest.relative_to(base)), "destination exists")

    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))

    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "target_dir": target_dir,
        "dryRun": dry_run,
        "moved": not dry_run,
        "source": str(relative),
        "target": str(dest.relative_to(base)),
        "bytes": dest.stat().st_size if dest.exists() else source.stat().st_size,
    }


@conn.handler("file/command/move", isolated=True, external=True,
              meta={"label": "Move or rename one file to a sandboxed path"})
def move_file(
    path: str = "",
    target_path: str = "",
    dry_run: bool = True,
    overwrite: bool = False,
    make_parents: bool = True,
) -> dict[str, Any]:
    """Move one file under the sandbox root to another sandboxed file path."""
    if not path:
        return _error(path, "path is required")
    if not target_path:
        return _error(target_path, "target_path is required")
    source = _resolve(path)
    if source is None:
        return _error(path, "path escapes the sandbox root")
    if not source.exists():
        return _error(path, "path does not exist")
    if not source.is_file():
        return _error(path, "path is not a file")

    base = root()
    dest = _resolve(target_path)
    if dest is None:
        return _error(target_path, "target_path escapes the sandbox root")
    try:
        relative_source = source.relative_to(base)
        relative_target = dest.relative_to(base)
    except ValueError:
        return _error(path, "path escapes the sandbox root")

    if source == dest:
        return {
            "ok": True,
            "connector": CONNECTOR_ID,
            "path": path,
            "target_path": target_path,
            "dryRun": dry_run,
            "moved": False,
            "reason": "already at destination",
            "source": str(relative_source),
            "target": str(relative_target),
            "bytes": source.stat().st_size,
        }
    if dest.exists():
        if dest.is_dir():
            return _error(str(relative_target), "destination is a directory")
        if not overwrite:
            return _error(str(relative_target), "destination exists")
    if not make_parents and not dest.parent.exists():
        return _error(str(relative_target), "destination parent does not exist")

    size = source.stat().st_size
    if not dry_run:
        if make_parents:
            dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and overwrite:
            dest.unlink()
        shutil.move(str(source), str(dest))

    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "path": path,
        "target_path": target_path,
        "dryRun": dry_run,
        "moved": not dry_run,
        "source": str(relative_source),
        "target": str(relative_target),
        "bytes": size,
    }


# --- authoring surface: bindings / manifest / CLI --------------------------

def urirun_bindings() -> dict[str, Any]:
    """Serializable v2 bindings for this connector (entry point: urirun.bindings)."""
    return conn.bindings()


def connector_manifest() -> dict[str, Any]:
    """Full manifest: prose (connector.manifest.json) + routes/uriSchemes/
    adapterKinds/examples derived from the handlers."""
    return conn.manifest(urirun.load_manifest(__package__))


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: subcommands + dispatch derived from the handlers."""
    return conn.cli(argv, manifest_prose=urirun.load_manifest(__package__))


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
