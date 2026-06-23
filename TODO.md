# TODO

- [x] Expose `urirun_bindings()` through the stable `urirun.bindings`
      entry-point group.
- [ ] Add a Docker smoke (`docker-test` target + compose) matching the other
      `urirun-connector-*` repos, then extend CI to run it.
- [ ] Add an opt-in write/delete profile guarded behind an explicit policy flag.
- [ ] Add example flows that combine `fs://` with `planfile://` and `log://`.
- [ ] Promote the hub catalog entry from `planned` to `available` once published.
