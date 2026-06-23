.PHONY: help manifest bindings smoke test
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-10s %s\n",$$1,$$2}'
manifest: ## Print the connector manifest
	urirun-mcp-filesystem manifest
bindings: ## Print urirun bindings
	urirun-mcp-filesystem bindings
smoke: ## bindings -> urirun connectors smoke (validate/compile/run/MCP/A2A)
	urirun-mcp-filesystem bindings | urirun connectors smoke - \
	  --run 'fs://host/dir/query/list' --payload '{"path":"."}' \
	  --allow 'fs://host/*' --name mcp-filesystem
test: ## Install editable + smoke
	pip install -e . && python3 -m pytest -q && $(MAKE) smoke
