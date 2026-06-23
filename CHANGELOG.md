# Changelog

## [0.1.0] - 2026-06-20

### Added
- Initial MCP Filesystem connector: sandboxed read-only `fs://` routes for
  `dir/query/list`, `file/query/read` and `path/query/stat`, declared with
  `@connector.command` and projectable to MCP tools / A2A skills.
- Sandbox boundary under `IFURI_FS_ROOT`; paths escaping the root are rejected.
- CLI (`urirun-mcp-filesystem`), connector manifest, pytest suite, smoke target
  and GitHub Actions CI.
- `urirun.bindings` entry point for automatic `urirun discover` integration.
