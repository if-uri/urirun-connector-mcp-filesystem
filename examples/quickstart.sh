#!/usr/bin/env bash
# mcp-filesystem: install once, then run — auto-discovered, no registry path.
set -euo pipefail
urirun install urirun-connector-mcp-filesystem            # local dev: pip install -e .
urirun run 'fs://host/dir/query/list' --payload '{"path": "."}' --execute --allow 'fs://*'
