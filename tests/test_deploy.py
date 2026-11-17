"""Exercise the release script with fake Docker; no daemon or credentials used."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/deploy.sh'
FAKE = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]; cmd=Path(sys.argv[0]).name
with open(os.environ['CALL_LOG'],'a') as f: f.write(json.dumps([cmd,*args])+'\\n')
if cmd=='docker':
 if args[0]=='inspect':
  print('sha256:original' if args[2]=='{{.Image}}' else 'running healthy')
 elif args[0]=='compose':
  if 'ps' in args: print('container-id')
  if 'up' in args and 'previous.json' in ' '.join(args):
   if os.environ['CASE']=='rollback-failure': sys.exit(1)
   data=json.load(open(args[args.index('-f')+1]))
   assert data['services']['api']['image']=='sha256:original'
  if 'pull' in args and os.environ['CASE']=='pull-failure': sys.exit(1)
elif cmd=='curl' and os.environ['CASE'] in ['health-failure','rollback-failure']: sys.exit(1)
'''

class DeployTests(unittest.TestCase):
    def exercise(self, case, mapping=None):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); binaries=root/'bin'; binaries.mkdir()
            for command in ['docker','curl','sleep']:
                f=binaries/command; f.write_text(FAKE); f.chmod(0o700)
            source=root/'compose.json'
            original={'services': {'api': {'image':'stale:tag'}, 'database': {'image':'postgres:16'}}}
            source.write_text(json.dumps(original))
            env={**os.environ, 'PATH':str(binaries)+':'+os.environ['PATH'],
                 'CALL_LOG':str(root/'calls'), 'CASE':case, 'ACR_REGISTRY':'registry.example',
                 'ACR_NAMESPACE':'team', 'ACR_USERNAME':'test', 'ACR_PASSWORD':'fake',
                 'COMPOSE_FILE':str(source), 'PROJECT_NAME':'test',
                 'DEPLOY_SERVICES_JSON':json.dumps(mapping or {'api':'server'}),
                 'LOCAL_HEALTH_URL':'http://example/health', 'HEALTH_ATTEMPTS':'1'}
            result=subprocess.run(['bash',str(SCRIPT),'v1'],env=env,capture_output=True,text=True)
            calls=[json.loads(line) for line in (root/'calls').read_text().splitlines()] if (root/'calls').exists() else []
            data=json.loads(source.read_text())
            self.assertFalse(list(root.glob('.release.*/auth')), 'temporary credentials left behind')
            return result,data,calls,original

    def test_success_persists_only_selected_image(self):
        result,data,calls,_=self.exercise('success')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(data['services']['api']['image'],'registry.example/team/server:v1')
        self.assertEqual(data['services']['database']['image'],'postgres:16')
        self.assertFalse(any('--remove-orphans' in call for call in calls))

    def test_health_failure_restores_actual_image(self):
        result,data,calls,original=self.exercise('health-failure')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(data['services']['api']['image'],'sha256:original')
        self.assertEqual(data['services']['database'],original['services']['database'])
        self.assertIn('Previous images restored', result.stderr)
        self.assertNotIn('ROLLBACK FAILED', result.stderr)
        self.assertTrue(any('up' in call and '--pull' in call and 'never' in call for call in calls))

    def test_failed_rollback_is_reported_without_persisting_it(self):
        result,data,_,original=self.exercise('rollback-failure')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(data,original)
        self.assertIn('ROLLBACK FAILED', result.stderr)
        self.assertNotIn('Previous images restored', result.stderr)

    def test_pull_failure_does_not_touch_running_containers(self):
        result,data,calls,original=self.exercise('pull-failure')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(data,original)
        self.assertFalse(any('up' in call for call in calls))

    def test_unknown_service_rejected_before_login(self):
        result,data,calls,original=self.exercise('invalid',{'unknown':'server'})
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(data,original)
        self.assertEqual(calls,[])

if __name__ == '__main__': unittest.main()
