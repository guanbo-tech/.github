"""Linux-only real Caddy validation/reload/TLS test; never targets production."""
import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

HELPER = Path(__file__).resolve().parents[1] / 'scripts/domain-routes.py'
root = Path(tempfile.mkdtemp(prefix='ecs-domain-integration.'))
root.chmod(0o700)
name = 'ecs-domain-test-' + str(os.getpid())
source = root / 'Caddyfile'
original = '''{
 admin 127.0.0.1:22019
 local_certs
 http_port 18088
 https_port 14443
}
existing.seewiki.net {
 respond "baseline"
}
'''
source.write_text(original)
inode = source.stat().st_ino
release = root / 'release'
release.mkdir()
(root / 'www').mkdir()
(root / 'www/index.html').write_text('isolated upstream')
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root / 'www'))
server = http.server.ThreadingHTTPServer(('127.0.0.1', 18099), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env = {**os.environ, 'ECS_EDGE_CONTAINER': name, 'ECS_CADDY_ADMIN': '127.0.0.1:22019',
       'DOMAIN_ROUTES_JSON': json.dumps({'route-probe.seewiki.net': 18099})}

def cmd(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)

def helper(mode, check=True):
    return subprocess.run(['python3', str(HELPER), mode, str(source), str(release), 'integration'],
                          env=env, capture_output=True, text=True, check=check)

def https(domain, expected):
    ca = root / 'data/caddy/pki/authorities/local/root.crt'
    for _ in range(30):
        result = cmd('curl', '-sS', '--max-time', '2', '--cacert', str(ca), '--connect-to',
                     f'{domain}:443:127.0.0.1:14443', f'https://{domain}/', check=False)
        if result.returncode == 0 and expected in result.stdout:
            return
        time.sleep(1)
    raise AssertionError('TLS/upstream verification failed: ' + domain)

try:
    cmd('docker', 'run', '-d', '--name', name, '--network', 'host',
        '-v', f'{source}:/etc/caddy/Caddyfile:ro', '-v', f'{root}/data:/data', 'caddy:2')
    https('existing.seewiki.net', 'baseline')
    helper('prepare')
    assert source.read_text() == original
    helper('apply')
    https('route-probe.seewiki.net', 'isolated upstream')
    https('existing.seewiki.net', 'baseline')
    redirect = cmd('curl', '-sS', '-I', '--max-time', '5', '-H', 'Host: route-probe.seewiki.net',
                   'http://127.0.0.1:18088/').stdout
    assert '308' in redirect and 'https://route-probe.seewiki.net' in redirect
    assert source.stat().st_ino == inode
    helper('restore')
    assert source.read_text() == original
    https('existing.seewiki.net', 'baseline')
    # An invalid reload must fail; recovery must restore both disk and live config.
    helper('prepare')
    with (release / 'Caddyfile.candidate').open('a') as f:
        f.write('\ninvalid { definitely_not_a_caddy_directive }\n')
    assert helper('apply', check=False).returncode != 0
    helper('restore')
    assert source.read_text() == original and source.stat().st_ino == inode
    https('existing.seewiki.net', 'baseline')
    # Invalid preflight must not alter a source file, even after a successful run.
    env['DOMAIN_ROUTES_JSON'] = json.dumps({'existing.seewiki.net': 18099})
    assert helper('prepare', check=False).returncode != 0
    assert source.read_text() == original
    result = 'PASS: real validate/reload, trusted TLS, redirect, unchanged foreign route, invalid reload recovery, inode preservation'
    (root / 'result.txt').write_text(result + '\n')
    print(result)
finally:
    cmd('docker', 'rm', '-f', name, check=False)
    server.shutdown()
    server.server_close()
    print('Private evidence:', root)
