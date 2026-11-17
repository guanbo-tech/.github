# ECS release runner

`deploy.sh` updates selected, already provisioned services in an existing JSON
Compose project. It requires Bash 4+, Python 3, flock, curl and Docker Compose v2.
The reusable workflow supplies the registry credentials, version, Compose path,
project name, service-to-image mapping and local health URL.

- Keeps a per-project lock and private release snapshots beside the Compose file.
- Records actual running image IDs before pulling candidates.
- Starts selected services with `--no-deps`; does not remove orphan containers.
- Checks running state, Docker health when configured, and the supplied health URL.
- Restores previous image IDs on update/health failure and returns failure to CI.
- Persists successful image versions to the source Compose file.
- Deletes temporary registry authentication on exit; retains recovery snapshots.

This is container rollback only. Database schema/data rollback and business-chain
verification remain separate gates. First provisioning, domain changes, multiple
replicas and a failed rollback require separate handling. Snapshot files contain
runtime configuration and must remain private on the host. Do not prune their
referenced images before the rollback retention period ends.

Validate on Linux without contacting Docker or any registry:

```sh
python3 tests/test_deploy.py -v
bash -n scripts/deploy.sh
actionlint -shellcheck='' .github/workflows/deploy-ecs-reusable.yml
```

Install only after the current deployment finishes, retaining the previous runner
for recovery. A successful mock test is not proof of production rollback.

## Declarative domain routes

An optional manifest object such as `"domain_routes": {"koubo-api.seewiki.net": 3400}`
creates an owned Caddy block using the Compose project name. Omit this key to
leave routing untouched; an explicit empty object removes that owner's routes.
Only lowercase seewiki.net subdomains and integer loopback ports are accepted.
Existing domains outside the owner's block, wildcard sites and imports fail
closed and require a deliberate one-time adoption/review. Existing manual routes
are never silently adopted or overwritten.

Install `domain-routes.py` alongside `deploy.sh`. The edge container must be named
`seewiki-edge`, with `/data/guanbo/edge/Caddyfile` bound to
`/etc/caddy/Caddyfile`. All managed updates share `/data/guanbo/edge/.routes.lock`;
manual editors must also honor that lock. Validation uses a private candidate
copied into the edge container before any production container change. Writing
preserves the Caddyfile inode for the existing single-file bind mount.

After local service health passes, reload Caddy and verify each HTTPS endpoint
with normal certificate validation. Root responses 200–499 count as reachable
(401/404 are legitimate API roots); 5xx and TLS failures roll back routes and
container images. This is route readiness, not business acceptance. Caddy's
normal automatic HTTPS supplies certificates and HTTP redirects. DNS must already
resolve to the ECS, and ACME/ports 80 and 443 must be reachable. TLS issuance may
exceed the bounded readiness period and cause a retryable deployment failure.

Snapshots retain the complete private configuration. Process crashes/SIGKILL or
uncooperative manual edits require operator recovery; normal errors restore the
previous in-place file and reload it. Domain rollback failure is explicitly
reported. Production rollout still requires a real isolated Caddy/TLS rehearsal;
unit tests only verify ownership, conflict rejection and inode preservation.
