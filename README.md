# urirun-connector-mcp-filesystem

Sandboxed filesystem connector for [ifURI](https://ifuri.com) / [urirun](https://github.com/if-uri/urirun),
in the spirit of MCP filesystem servers. Query routes list/read/stat/find
duplicates under one root. The guarded move command is also sandboxed and
defaults to dry-run.

Catalog page: <https://connect.ifuri.com/connectors/mcp-filesystem>

## Routes

| URI | Operation |
| --- | --- |
| `fs://host/dir/query/list` | list a directory |
| `fs://host/file/query/read` | read a text file (size-capped) |
| `fs://host/file/query/blob` | read a binary file as base64 (size-capped) |
| `fs://host/path/query/stat` | stat a path |
| `fs://host/dir/command/prune_empty` | remove empty directories under a sandboxed path |
| `fs://host/duplicates/query/find` | find exact duplicate files by SHA-256 |
| `fs://host/file/command/write_text` | write a UTF-8 text file under the sandbox root |
| `fs://host/file/command/move_to_dir` | move one file into a sandboxed directory |
| `fs://host/file/command/move` | move or rename one file to a sandboxed path |

## Sandbox

Everything resolves under `IFURI_FS_ROOT` (default: the current working
directory). Paths that escape the root are rejected with `ok: false`.
Query routes never write or delete. `fs://host/dir/command/prune_empty`,
`fs://host/file/command/write_text`, `fs://host/file/command/move_to_dir` and
`fs://host/file/command/move` are the mutating routes; they default to
`dry_run: true`. File writes and moves do not overwrite unless
`overwrite: true`.

## Install

```bash
pip install "urirun-connector-mcp-filesystem @ git+https://github.com/if-uri/urirun-connector-mcp-filesystem.git@v0.1.0"
# or, from the hub:
urirun connectors install mcp-filesystem --execute
```

## Use

```bash
export IFURI_FS_ROOT="$PWD"
urirun-mcp-filesystem bindings > bindings.json
urirun compile bindings.json --out registry.json
urirun run 'fs://host/dir/query/list' registry.json \
  --payload '{"path":"."}' --execute --allow 'fs://host/*'

urirun run 'fs://host/duplicates/query/find' registry.json \
  --payload '{"path":".","extensions":"pdf,png,jpg"}' \
  --execute --allow 'fs://host/*'

urirun run 'fs://host/file/query/blob' registry.json \
  --payload '{"path":"2026.05/invoice.pdf","max_bytes":10485760}' \
  --execute --allow 'fs://host/*'

urirun run 'fs://host/file/command/move_to_dir' registry.json \
  --payload '{"path":"2026.05/manual.pdf","target_dir":"no_invoice","dry_run":true}' \
  --execute --allow 'fs://host/*'

urirun run 'fs://host/file/command/move' registry.json \
  --payload '{"path":"2026.05/_by_supplier/saas/me/invoice.pdf","target_path":"2026.05/2026.05.12-saas-invoice.pdf","dry_run":true}' \
  --execute --allow 'fs://host/*'

urirun run 'fs://host/dir/command/prune_empty' registry.json \
  --payload '{"path":".","exclude":"no_invoice","dry_run":true}' \
  --execute --allow 'fs://host/*'

urirun run 'fs://host/file/command/write_text' registry.json \
  --payload '{"path":"index.php","content":"<?php echo \"ok\";","dry_run":true}' \
  --execute --allow 'fs://host/*'
```

After installation, `urirun` can discover this connector automatically through
the `urirun.bindings` entry-point group:

```bash
urirun discover --out connectors.bindings.json --registry-out connectors.registry.json
urirun list --entry-points
```

The connector projects to MCP tools and A2A skills like any other urirun
connector (`python3 -m urirun.v2_mcp tools registry.json`).

## License

Released under the terms in [LICENSE](LICENSE).
