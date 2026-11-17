#!/usr/bin/env bash
# Linux integration test: real registry, Compose updates, failure and rollback.
# Uses only loopback ports 15000/18099 and its own disposable project.
set -Eeuo pipefail
umask 077
root="$(mktemp -d /tmp/ecs-runner-integration.XXXXXXXX)"
project="runner-test-$$"
registry="${project}-registry"
runner="$(cd "$(dirname "$0")/.." && pwd)/scripts/deploy.sh"
real_registry="127.0.0.1:15000"
docker image inspect caddy:2 registry:2 >/dev/null
cleanup() {
  docker compose -p "$project" -f "$root/compose.json" down >/dev/null 2>&1 || true
  docker rm -f "$registry" >/dev/null 2>&1 || true
  echo "Evidence retained at $root"
}
trap cleanup EXIT
docker run -d --name "$registry" -p 127.0.0.1:15000:5000 registry:2 >/dev/null
for _ in $(seq 1 20); do curl -fsS "http://$real_registry/v2/" >/dev/null && break; sleep 1; done
docker tag caddy:2 "$real_registry/test/probe:good"
docker push "$real_registry/test/probe:good" >/dev/null
printf 'FROM caddy:2\nENTRYPOINT ["/bin/false"]\n' > "$root/Dockerfile"
docker build -q -t "$real_registry/test/probe:bad" "$root" >/dev/null
docker push "$real_registry/test/probe:bad" >/dev/null
cat > "$root/compose.json" <<'JSON'
{"services":{"probe":{"image":"caddy:2","command":["caddy","file-server","--listen",":8080","--root","/usr/share/caddy"],"ports":["127.0.0.1:18099:8080"]}}}
JSON
docker compose -p "$project" -f "$root/compose.json" up -d >/dev/null
export ACR_REGISTRY="$real_registry" ACR_NAMESPACE=test ACR_USERNAME=test ACR_PASSWORD=test
export COMPOSE_FILE="$root/compose.json" PROJECT_NAME="$project"
export DEPLOY_SERVICES_JSON='{"probe":"probe"}' LOCAL_HEALTH_URL=http://127.0.0.1:18099/ HEALTH_ATTEMPTS=3
bash "$runner" good > "$root/success.log" 2>&1
python3 - "$COMPOSE_FILE" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))['services']['probe']['image'].endswith(':good')
PY
current="$(docker compose -p "$project" -f "$COMPOSE_FILE" ps -q probe)"
old_id="$(docker inspect --format '{{.Image}}' "$current")"
if bash "$runner" bad > "$root/failure.log" 2>&1; then echo 'Bad candidate unexpectedly succeeded'; exit 1; fi
grep -q 'Previous images restored' "$root/failure.log"
id="$(docker compose -p "$project" -f "$COMPOSE_FILE" ps -q probe)"
test "$(docker inspect --format '{{.Image}}' "$id")" = "$old_id"
python3 - "$COMPOSE_FILE" "$old_id" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))['services']['probe']['image']==sys.argv[2]
PY
for _ in $(seq 1 20); do curl -fsS "$LOCAL_HEALTH_URL" >/dev/null && break; sleep 1; done
curl -fsS "$LOCAL_HEALTH_URL" >/dev/null
test -z "$(find "$root" -type d -name auth -print -quit)"
echo 'PASS: real pull, successful release, failed candidate, restored image, persisted rollback and HTTP recovery'
