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
