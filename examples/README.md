# mcp-filesystem connector — examples

Read files and directories (read-only).

## Install
```bash
urirun install urirun-connector-mcp-filesystem
```
`urirun install` resolves catalog ids via connect.ifuri.com; `--catalog <url>` points at a
local/on-prem registry; a full package name / git URL / path falls back to `pip install`.

## Run
```bash
# Read files and directories (read-only) (read)
urirun run 'fs://host/dir/query/list' --payload '{"path": "."}' --execute --allow 'fs://*'

# preview without running (dry-run): drop --execute
urirun run 'fs://host/dir/query/list' --payload '{"path": "."}' --allow 'fs://*'
```

## Inspect the runtime (no path — like error:// / log://)
```bash
urirun list | grep 'fs://'                                   # this connector's routes
urirun run 'registry://local/routes/query/list' --payload '{"scheme":"fs"}' --allow 'registry://*'
urirun run 'registry://local/bindings/query/show' --payload '{"uri":"fs://host/dir/query/list"}' --allow 'registry://*'   # full typed contract
urirun errors                                                      # recent runtime errors (error://)
```

## Generate a client / API surface from the binding
```bash
urirun discover | urirun gen openapi - --out openapi.json   # OpenAPI 3 (one path per route)
urirun discover | urirun gen proto   - --out service.proto  # protobuf + gRPC (typed rpc per route)
urirun discover | urirun gen client  - --out client.py      # typed Python client
```
