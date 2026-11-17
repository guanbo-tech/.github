"""Owned Caddy blocks. Never print the existing configuration (may contain ACLs)."""
import json, os, re, subprocess, sys
from pathlib import Path


def render(text, owner, routes):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', owner):
        raise ValueError('Invalid route owner')
    if not isinstance(routes, dict):
        raise ValueError('Routes must be an object')
    begin, end = f'# BEGIN ECS ROUTES {owner}', f'# END ECS ROUTES {owner}'
    lines = text.splitlines(keepends=True)
    starts = [i for i, x in enumerate(lines) if x.rstrip('\r\n') == begin]
    ends = [i for i, x in enumerate(lines) if x.rstrip('\r\n') == end]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError('Malformed owned block')
        outside = ''.join(lines[:starts[0]] + lines[ends[0]+1:])
        if any('# BEGIN ECS ROUTES' in x or '# END ECS ROUTES' in x
               for x in lines[starts[0]+1:ends[0]]):
            raise ValueError('Nested route block')
    else:
        outside = text
    if re.search(r'(?m)^\s*import\s', outside):
        raise ValueError('Imported configuration requires explicit conflict review')
    blocks = []
    for domain, port in sorted(routes.items()):
        if not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+seewiki\.net', domain):
            raise ValueError('Only seewiki.net subdomains are supported')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid loopback port')
        if domain in outside:
            raise ValueError('Domain already exists outside this project block')
        # A wildcard site can intercept a newly introduced domain; fail closed.
        if re.search(r'\*\.[a-z0-9.-]*seewiki\.net', outside):
            raise ValueError('Existing wildcard requires explicit operator review')
        blocks.append(f'{domain} {{\n    reverse_proxy 127.0.0.1:{port}\n}}\n')
    return outside.rstrip('\n') + '\n\n' + begin + '\n' + ''.join(blocks) + end + '\n'


def write_in_place(path, data):
    # Preserve inode: the live container has a single-file bind mount.
    with open(path, 'r+b') as f:
        f.seek(0); f.write(data); f.truncate(); f.flush(); os.fsync(f.fileno())


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    mode, source, release, owner = sys.argv[1:]
    source, release = Path(source), Path(release)
    if source.is_symlink() or not source.is_file():
        raise ValueError('Caddyfile must be a regular file')
    edge = os.environ.get('ECS_EDGE_CONTAINER', 'seewiki-edge')
    backup, candidate = release/'Caddyfile.previous', release/'Caddyfile.candidate'
    if mode == 'prepare':
        routes = json.loads(os.environ['DOMAIN_ROUTES_JSON'])
        text = source.read_text()
        candidate.write_text(render(text, owner, routes))
        backup.write_bytes(source.read_bytes())
        run('docker','cp',str(candidate),edge+':/tmp/ecs-candidate.Caddyfile')
        try:
            run('docker','exec',edge,'caddy','validate','--config','/tmp/ecs-candidate.Caddyfile','--adapter','caddyfile')
        finally:
            run('docker','exec',edge,'rm','-f','/tmp/ecs-candidate.Caddyfile')
    elif mode in ('apply','restore'):
        write_in_place(source, (candidate if mode == 'apply' else backup).read_bytes())
        address = ['--address', os.environ['ECS_CADDY_ADMIN']] if os.environ.get('ECS_CADDY_ADMIN') else []
        run('docker','exec',edge,'caddy','reload','--config','/etc/caddy/Caddyfile','--adapter','caddyfile',*address)
    else:
        raise ValueError('Unknown route operation')

if __name__ == '__main__':
    try: main()
    except Exception:
        sys.exit('Domain route operation failed; private recovery files retained')
