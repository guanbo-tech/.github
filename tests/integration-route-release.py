"""Run the full release test against an isolated Caddy with a private test CA."""
import os
import fcntl
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

root = Path(tempfile.mkdtemp(prefix='ecs-route-release.'))
root.chmod(0o700)
name = 'ecs-route-release-' + str(os.getpid())
source = root / 'Caddyfile'
source.write_text('''{
 admin 127.0.0.1:22019
 local_certs
 http_port 18088
 https_port 14443
}
existing.seewiki.net {
 respond "baseline"
}
''')
binary = root / 'bin'
binary.mkdir()
curl = shutil.which('curl')
wrapper = binary / 'curl'
wrapper.write_text('''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
if any(x.startswith('https://') for x in args):
 args=['--connect-to','route-probe.seewiki.net:443:127.0.0.1:14443',
       '--cacert',os.environ['TEST_CA'],*args]
os.execv(os.environ['REAL_CURL'],[os.environ['REAL_CURL'],*args])
''')
wrapper.chmod(0o700)
env = {**os.environ, 'ECS_EDGE_CONTAINER': name, 'ECS_CADDY_ADMIN': '127.0.0.1:22019',
       'ECS_CADDYFILE': str(source), 'DOMAIN_ROUTES_JSON': '{"route-probe.seewiki.net":18099}',
       'DOMAIN_ROUTE_TEST': '1', 'PATH': str(binary)+':'+os.environ['PATH'],
       'REAL_CURL': curl, 'TEST_CA': str(root/'data/caddy/pki/authorities/local/root.crt')}
try:
    subprocess.run(['docker','run','-d','--name',name,'--network','host','-v',
                    f'{source}:/etc/caddy/Caddyfile:ro','-v',f'{root}/data:/data','caddy:2'],
                   check=True, stdout=subprocess.DEVNULL)
    for _ in range(30):
        if Path(env['TEST_CA']).exists(): break
        time.sleep(1)
    assert Path(env['TEST_CA']).exists(), 'Test Caddy failed to create its CA'
    # Another project holding the common edge lock prevents even preparation.
    lock_compose = root / 'lock-compose.json'
    lock_compose.write_text('{"services":{}}')
    lock_env = {**env, 'ACR_REGISTRY':'unused.invalid', 'ACR_NAMESPACE':'test',
                'ACR_USERNAME':'test', 'ACR_PASSWORD':'test',
                'COMPOSE_FILE':str(lock_compose), 'PROJECT_NAME':'lock-test',
                'DEPLOY_SERVICES_JSON':'{}'}
    runner = Path(__file__).resolve().parents[1] / 'scripts/deploy.sh'
    with (root / '.routes.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        process = subprocess.Popen(['bash',str(runner),'v-lock-test'], env=lock_env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1)
        assert process.poll() is None, 'Runner bypassed the global edge lock'
        assert not list(root.glob('.release.*/Caddyfile.previous')), 'Prepared while another writer held the lock'
        fcntl.flock(lock, fcntl.LOCK_UN)
        _, error = process.communicate(timeout=15)
        assert process.returncode != 0 and 'Empty service mapping' in error
    print('PASS: runner waits for the shared edge lock before preparing configuration')
    with (root/'result.log').open('w') as log:
        result = subprocess.run(['bash',str(Path(__file__).parent/'integration-deploy.sh')],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
    print((root/'result.log').read_text())
    if result.returncode: raise SystemExit(result.returncode)
finally:
    subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,check=False)
    print('Private evidence:', root)
