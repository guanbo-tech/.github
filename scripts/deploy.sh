#!/usr/bin/env bash
# Run on ECS. Compose files are JSON and contain existing runtime configuration.
set -Eeuo pipefail
umask 077
# A wrapper inherits the deadline established at the remote command entry.
export ECS_RELEASE_DEADLINE_EPOCH="${ECS_RELEASE_DEADLINE_EPOCH:-$(($(date +%s) + 450))}"
[[ "$ECS_RELEASE_DEADLINE_EPOCH" =~ ^[0-9]+$ ]]
VERSION="${1:?usage: deploy.sh <version>}"
: "${ACR_REGISTRY:?}" "${ACR_NAMESPACE:?}" "${ACR_USERNAME:?}" "${ACR_PASSWORD:?}"
: "${COMPOSE_FILE:?}" "${PROJECT_NAME:?}" "${DEPLOY_SERVICES_JSON:?}"
[[ "$VERSION" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]
[[ "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]]
[[ "$COMPOSE_FILE" = /*.json && -f "$COMPOSE_FILE" && ! -L "$COMPOSE_FILE" ]]
HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-30}"
[[ "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]
exec 9>"$(dirname "$COMPOSE_FILE")/.deployment.lock"
flock -n 9 || { echo 'Another deployment is active'; exit 1; }
release="$(mktemp -d "$(dirname "$COMPOSE_FILE")/.release.XXXXXXXX")"
export DOCKER_CONFIG="$release/auth"
mkdir "$DOCKER_CONFIG"
candidate="$release/candidate.json"
previous="$release/previous.json"
rollback_needed=0
routes_applied=0
route_helper="$(dirname "$0")/domain-routes.py"
route_file=/data/guanbo/edge/Caddyfile
compose() { docker compose --project-directory "$(dirname "$COMPOSE_FILE")" -p "$PROJECT_NAME" -f "$1" "${@:2}"; }
finish() {
  result=$?
  trap - EXIT
  if (( routes_applied )); then
    python3 "$route_helper" restore "$route_file" "$release" "$PROJECT_NAME" || echo 'DOMAIN ROLLBACK FAILED; inspect private release snapshot' >&2
  fi
  if (( rollback_needed )); then
    echo 'Deployment failed; restoring previous container images' >&2
    if compose "$previous" up -d --no-build --no-deps --pull never "${services[@]}" &&
       cp "$previous" "$COMPOSE_FILE.next" && mv "$COMPOSE_FILE.next" "$COMPOSE_FILE"; then
      echo 'Previous images restored; business health still requires verification' >&2
    else
      echo "ROLLBACK FAILED: recovery configuration retained at $previous" >&2
    fi
  fi
  rm -rf "$DOCKER_CONFIG"
  exit "$result"
}
trap finish EXIT
if [[ -n "${DOMAIN_ROUTES_JSON:-}" ]]; then
  # Hold across validation, update, health checks and final persistence.
  exec 7>/data/guanbo/edge/.routes.lock
  flock -w 120 7 || { echo 'Another edge update is active'; exit 1; }
  python3 "$route_helper" prepare "$route_file" "$release" "$PROJECT_NAME"
fi
# Reject invalid mappings before authenticating or changing any containers.
python3 - "$COMPOSE_FILE" "$candidate" "$DEPLOY_SERVICES_JSON" "$ACR_REGISTRY" "$ACR_NAMESPACE" "$VERSION" "$release/services" <<'PY'
import json,re,sys
source,target,mapping,registry,namespace,version,names=sys.argv[1:]
data=json.load(open(source)); mapping=json.loads(mapping)
if not isinstance(mapping,dict) or not mapping: raise SystemExit('Empty service mapping')
for service,image in mapping.items():
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',service): raise SystemExit('Invalid Compose service')
    if service not in data.get('services',{}): raise SystemExit('Unknown Compose service: '+service)
    if not isinstance(image,str) or not re.fullmatch(r'[a-z0-9]+(?:[._/-][a-z0-9]+)*',image): raise SystemExit('Invalid image name')
    data['services'][service]['image']=f'{registry}/{namespace}/{image}:{version}'
with open(target,'w') as f: json.dump(data,f,indent=2); f.write('\n')
with open(names,'w') as f: f.write('\n'.join(mapping)+'\n')
PY
mapfile -t services < "$release/services"
compose "$candidate" config --quiet
cp "$COMPOSE_FILE" "$previous"
# Roll back to the actual running image IDs, not possibly stale source tags.
for service in "${services[@]}"; do
  id="$(compose "$COMPOSE_FILE" ps -q "$service")"
  [[ -n "$id" && "$id" != *$'\n'* ]] || { echo "Expected one running container: $service"; exit 1; }
  old_image="$(docker inspect --format '{{.Image}}' "$id")"
  python3 - "$previous" "$service" "$old_image" <<'PY'
import json,sys
path,service,image=sys.argv[1:]; data=json.load(open(path))
data['services'][service]['image']=image
with open(path,'w') as f: json.dump(data,f,indent=2); f.write('\n')
PY
done
printf '%s' "$ACR_PASSWORD" | docker login "$ACR_REGISTRY" --username "$ACR_USERNAME" --password-stdin >/dev/null
compose "$candidate" pull "${services[@]}"
rollback_needed=1
compose "$candidate" up -d --no-build --no-deps "${services[@]}"
healthy=0
for ((attempt=0; attempt<HEALTH_ATTEMPTS; attempt++)); do
  healthy=1
  for service in "${services[@]}"; do
    id="$(compose "$candidate" ps -q "$service")"
    state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id" 2>/dev/null || true)"
    [[ "$state" = 'running healthy' || "$state" = 'running none' ]] || healthy=0
  done
  if [[ -n "${LOCAL_HEALTH_URL:-}" ]]; then
    curl --fail --silent --show-error --connect-timeout 3 --max-time 8 "$LOCAL_HEALTH_URL" >/dev/null || healthy=0
  fi
  (( healthy )) && break
  sleep 5
done
(( healthy )) || { echo 'Candidate health check failed'; exit 1; }
if [[ -n "${DOMAIN_ROUTES_JSON:-}" ]]; then
  routes_applied=1
  python3 "$route_helper" apply "$route_file" "$release" "$PROJECT_NAME"
  # Verify TLS and HTTP availability before committing the release.
  # The absolute deadline includes time spent in repository-specific wrappers.
  # Reserve the remote command's final 150s for normal rollback.
  python3 - <<'PYROUTES'
import json, os, subprocess, time
budget = min(90, int(os.environ['ECS_RELEASE_DEADLINE_EPOCH']) - time.time())
deadline = time.monotonic() + max(0, budget)
pending = list(json.loads(os.environ['DOMAIN_ROUTES_JSON']))
while pending:
    for domain in list(pending):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit('HTTPS routes exceeded shared readiness deadline')
        result = subprocess.run(['curl','--silent','--show-error',
                                 '--connect-timeout',str(min(5, remaining)),
                                 '--max-time',str(min(10, remaining)),
                                 '--output','/dev/null','--write-out','%{http_code}',
                                 f'https://{domain}/'], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.isdigit() and 200 <= int(result.stdout) < 500:
            pending.remove(domain)
    if pending:
        time.sleep(min(2, max(0, deadline - time.monotonic())))
PYROUTES
fi
cp "$candidate" "$COMPOSE_FILE.next"
mv "$COMPOSE_FILE.next" "$COMPOSE_FILE"
rollback_needed=0
routes_applied=0
echo "Deployed $VERSION; previous images recorded in $previous"
