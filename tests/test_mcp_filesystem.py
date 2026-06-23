# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import json

import pytest

import urirun
from urirun import v2
from urirun_connector_mcp_filesystem import (
    connector_manifest,
    find_duplicates,
    list_dir,
    main,
    move_file,
    move_to_dir,
    prune_empty_dirs,
    read_blob,
    read_file,
    stat_path,
    urirun_bindings,
    write_text_file,
)
from urirun_connector_mcp_filesystem.core import DEFAULT_MAX_BYTES

ROUTE_LIST = "fs://host/dir/query/list"
ROUTE_READ = "fs://host/file/query/read"
ROUTE_BLOB = "fs://host/file/query/blob"
ROUTE_STAT = "fs://host/path/query/stat"
ROUTE_PRUNE_EMPTY = "fs://host/dir/command/prune_empty"
ROUTE_WRITE_TEXT = "fs://host/file/command/write_text"
ROUTE_DUPLICATES = "fs://host/duplicates/query/find"
ROUTE_MOVE = "fs://host/file/command/move_to_dir"
ROUTE_MOVE_FILE = "fs://host/file/command/move"
ALL_ROUTES = {
    ROUTE_LIST,
    ROUTE_READ,
    ROUTE_BLOB,
    ROUTE_STAT,
    ROUTE_PRUNE_EMPTY,
    ROUTE_WRITE_TEXT,
    ROUTE_DUPLICATES,
    ROUTE_MOVE,
    ROUTE_MOVE_FILE,
}


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    (tmp_path / "hello.txt").write_text("hi there", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("nested", encoding="utf-8")
    monkeypatch.setenv("IFURI_FS_ROOT", str(tmp_path))
    return tmp_path


# --- real impl functions called directly ---

def test_list_dir(sandbox):
    result = list_dir(".")
    assert result["ok"] is True
    names = {entry["name"] for entry in result["entries"]}
    assert {"hello.txt", "sub"} <= names


def test_list_dir_expands_home_inside_sandbox(sandbox, monkeypatch):
    monkeypatch.setenv("HOME", str(sandbox))

    result = list_dir("~/sub")

    assert result["ok"] is True
    assert result["path"] == "~/sub"
    assert [entry["name"] for entry in result["entries"]] == ["nested.txt"]


def test_read_file(sandbox):
    result = read_file("hello.txt")
    assert result["ok"] is True
    assert result["content"] == "hi there"
    assert result["truncated"] is False


def test_read_file_truncates(sandbox):
    result = read_file("hello.txt", max_bytes=2)
    assert result["truncated"] is True
    assert result["content"] == "hi"


def test_read_blob_returns_base64_and_hash(sandbox):
    (sandbox / "invoice.pdf").write_bytes(b"%PDF fake")
    result = read_blob("invoice.pdf")
    assert result["ok"] is True
    assert result["name"] == "invoice.pdf"
    assert result["mime"] == "application/pdf"
    assert result["size"] == len(b"%PDF fake")
    assert result["bytes_b64"] == "JVBERiBmYWtl"
    assert len(result["sha256"]) == 64


def test_read_blob_respects_max_bytes(sandbox):
    (sandbox / "big.bin").write_bytes(b"12345")
    result = read_blob("big.bin", max_bytes=4)
    assert result["ok"] is False
    assert "exceeds max_bytes" in result["error"]


def test_stat_path(sandbox):
    result = stat_path("sub")
    assert result["ok"] is True
    assert result["type"] == "dir"


def test_sandbox_blocks_traversal(sandbox):
    result = read_file("../outside.txt")
    assert result["ok"] is False
    assert "escapes the sandbox" in result["error"]


def test_missing_path(sandbox):
    assert read_file("nope.txt")["ok"] is False
    assert stat_path("nope.txt")["ok"] is False


def test_find_duplicates_exact_hashes(sandbox):
    (sandbox / "copy-a.pdf").write_bytes(b"same invoice")
    (sandbox / "sub" / "copy-b.pdf").write_bytes(b"same invoice")
    (sandbox / "different.pdf").write_bytes(b"different")

    result = find_duplicates(".", extensions="pdf")

    assert result["ok"] is True
    assert result["groupCount"] == 1
    assert result["duplicateFiles"] == 2
    assert result["reclaimableBytes"] == len(b"same invoice")
    assert set(result["groups"][0]["paths"]) == {"copy-a.pdf", "sub/copy-b.pdf"}


def test_find_duplicates_respects_sandbox(sandbox):
    result = find_duplicates("../outside")
    assert result["ok"] is False
    assert "escapes the sandbox" in result["error"]


def test_move_to_dir_dry_run_and_execute(sandbox):
    (sandbox / "2026.05").mkdir()
    source = sandbox / "2026.05" / "not-invoice.pdf"
    source.write_bytes(b"manual")

    dry = move_to_dir("2026.05/not-invoice.pdf", target_dir="no_invoice")
    assert dry["ok"] is True
    assert dry["dryRun"] is True
    assert dry["moved"] is False
    assert dry["target"] == "no_invoice/2026.05/not-invoice.pdf"
    assert source.exists()

    moved = move_to_dir("2026.05/not-invoice.pdf", target_dir="no_invoice", dry_run=False)
    assert moved["ok"] is True
    assert moved["moved"] is True
    assert not source.exists()
    assert (sandbox / "no_invoice" / "2026.05" / "not-invoice.pdf").read_bytes() == b"manual"


def test_move_to_dir_refuses_existing_destination(sandbox):
    (sandbox / "a.pdf").write_bytes(b"a")
    (sandbox / "no_invoice").mkdir()
    (sandbox / "no_invoice" / "a.pdf").write_bytes(b"old")
    result = move_to_dir("a.pdf", preserve_relative=False, dry_run=False)
    assert result["ok"] is False
    assert "destination exists" in result["error"]


def test_move_file_dry_run_and_execute(sandbox):
    (sandbox / "2026.05" / "nested").mkdir(parents=True)
    source = sandbox / "2026.05" / "nested" / "invoice.pdf"
    source.write_bytes(b"invoice")

    target = "2026.05/2026.05.12-saas-invoice.pdf"
    dry = move_file("2026.05/nested/invoice.pdf", target_path=target)
    assert dry["ok"] is True
    assert dry["dryRun"] is True
    assert dry["moved"] is False
    assert dry["target"] == target
    assert source.exists()

    moved = move_file("2026.05/nested/invoice.pdf", target_path=target, dry_run=False)
    assert moved["ok"] is True
    assert moved["moved"] is True
    assert not source.exists()
    assert (sandbox / target).read_bytes() == b"invoice"


def test_move_file_refuses_escape_and_existing_destination(sandbox):
    (sandbox / "a.pdf").write_bytes(b"a")
    (sandbox / "b.pdf").write_bytes(b"b")

    escaped = move_file("a.pdf", target_path="../outside.pdf")
    assert escaped["ok"] is False
    assert "escapes the sandbox" in escaped["error"]

    existing = move_file("a.pdf", target_path="b.pdf", dry_run=False)
    assert existing["ok"] is False
    assert "destination exists" in existing["error"]


def test_write_text_file_dry_run_and_execute(sandbox):
    dry = write_text_file("panel/run.sh", "#!/usr/bin/env bash\n", mode="0755")
    assert dry["ok"] is True
    assert dry["dryRun"] is True
    assert dry["written"] is False
    assert dry["target"] == "panel/run.sh"
    assert not (sandbox / "panel" / "run.sh").exists()

    written = write_text_file("panel/run.sh", "#!/usr/bin/env bash\n", dry_run=False, mode="0755")
    assert written["ok"] is True
    assert written["written"] is True
    assert (sandbox / "panel" / "run.sh").read_text(encoding="utf-8") == "#!/usr/bin/env bash\n"
    assert oct((sandbox / "panel" / "run.sh").stat().st_mode & 0o777) == "0o755"


def test_write_text_file_refuses_escape_and_existing_destination(sandbox):
    (sandbox / "exists.txt").write_text("old", encoding="utf-8")

    escaped = write_text_file("../outside.txt", "x", dry_run=False)
    assert escaped["ok"] is False
    assert "escapes the sandbox" in escaped["error"]

    existing = write_text_file("exists.txt", "new", dry_run=False)
    assert existing["ok"] is False
    assert "destination exists" in existing["error"]
    assert (sandbox / "exists.txt").read_text(encoding="utf-8") == "old"


def test_prune_empty_dirs_dry_run_and_execute(sandbox):
    (sandbox / "2026.05" / "_by_supplier" / "saas").mkdir(parents=True)
    (sandbox / "2026.05" / "invoice.pdf").write_bytes(b"invoice")
    (sandbox / "no_invoice" / "2026.05" / "nested").mkdir(parents=True)

    dry = prune_empty_dirs(".")
    assert dry["ok"] is True
    assert dry["dryRun"] is True
    assert "2026.05/_by_supplier/saas" in dry["removed"]
    assert "2026.05/_by_supplier" in dry["removed"]
    assert all(not path.startswith("no_invoice/") for path in dry["removed"])

    moved = prune_empty_dirs(".", dry_run=False)
    assert moved["ok"] is True
    assert moved["removedCount"] == 2
    assert not (sandbox / "2026.05" / "_by_supplier").exists()
    assert (sandbox / "no_invoice" / "2026.05" / "nested").exists()


# --- v2 authoring contract: isolated handlers (registry-portable) ---

def test_bindings_are_isolated_handlers():
    b = urirun_bindings()["bindings"]
    assert set(b) == ALL_ROUTES
    module = "urirun_connector_mcp_filesystem.core"
    for route, export in (
        (ROUTE_LIST, "list_dir"),
        (ROUTE_READ, "read_file"),
        (ROUTE_BLOB, "read_blob"),
        (ROUTE_STAT, "stat_path"),
        (ROUTE_PRUNE_EMPTY, "prune_empty_dirs"),
        (ROUTE_WRITE_TEXT, "write_text_file"),
        (ROUTE_DUPLICATES, "find_duplicates"),
        (ROUTE_MOVE, "move_to_dir"),
        (ROUTE_MOVE_FILE, "move_file"),
    ):
        # registry-portable in-process handler: runs out-of-process via urirun.exec
        assert b[route]["adapter"] == "local-function-subprocess"
        assert b[route]["python"]["module"] == module
        assert b[route]["python"]["export"] == export
        assert "argv" not in b[route]
    assert b[ROUTE_LIST]["inputSchema"]["properties"]["path"]["default"] == "."
    assert b[ROUTE_READ]["inputSchema"]["properties"]["max_bytes"]["default"] == DEFAULT_MAX_BYTES
    json.dumps(urirun_bindings())  # serializable: no live ref leaks


def test_compiles_and_routes_present():
    registry = urirun.compile_registry(urirun_bindings())
    uris = {r["uri"] for r in urirun.list_routes(registry)}
    assert ALL_ROUTES <= uris


def test_runtime_executes_from_compiled_registry(tmp_path, monkeypatch):
    # the whole point: a serialized->compiled registry still runs the route
    (tmp_path / "hello.txt").write_text("hi there", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    monkeypatch.setenv("IFURI_FS_ROOT", str(tmp_path))

    registry = urirun.compile_registry(json.loads(json.dumps(urirun_bindings())))
    policy = urirun.policy(allow=["fs://*"])

    env = v2.run(ROUTE_LIST, registry, payload={"path": "."},
                 mode="execute", policy=policy)
    assert env["ok"] is True
    data = urirun.result_data(env)
    assert data["ok"] is True
    assert {"hello.txt", "sub"} <= {e["name"] for e in data["entries"]}

    env = v2.run(ROUTE_READ, registry, payload={"path": "hello.txt"},
                 mode="execute", policy=policy)
    assert env["ok"] is True
    assert urirun.result_data(env)["content"] == "hi there"

    env = v2.run(ROUTE_BLOB, registry, payload={"path": "hello.txt"},
                 mode="execute", policy=policy)
    assert env["ok"] is True
    assert urirun.result_data(env)["bytes_b64"] == "aGkgdGhlcmU="

    (tmp_path / "a.pdf").write_bytes(b"dup")
    (tmp_path / "sub" / "b.pdf").write_bytes(b"dup")
    env = v2.run(ROUTE_DUPLICATES, registry, payload={"path": ".", "extensions": "pdf"},
                 mode="execute", policy=policy)
    assert env["ok"] is True
    assert urirun.result_data(env)["groupCount"] == 1


def test_manifest_prose_plus_derived_routes():
    m = connector_manifest()
    assert m["id"] == "mcp-filesystem"
    assert set(m["routes"]) == ALL_ROUTES
    assert m["uriSchemes"] == ["fs"]
    assert m["summary"]  # prose preserved
    assert m["install"]["mode"] == "urirun-extra"
    json.dumps(m)


# --- CLI ---

def test_cli_bindings_and_manifest(capsys):
    assert main(["bindings"]) == 0
    assert ALL_ROUTES <= set(json.loads(capsys.readouterr().out)["bindings"])
    assert main(["manifest"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "mcp-filesystem"
