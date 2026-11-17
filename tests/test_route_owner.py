"""Run the actual release/route code; fake only external Docker and HTTP."""
import json, os, subprocess, tempfile, unittest
from pathlib import Path
from test_deploy import FAKE
SCRIPT=Path(__file__).parents[1]/'scripts/deploy.sh'
class RouteOwnerTests(unittest.TestCase):
 def test_independent_releases_in_one_project(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); b=root/'bin';b.mkdir()
   for name in ['docker','curl','sleep']:
    p=b/name;p.write_text(FAKE+'\nif cmd=="curl" and os.environ["CASE"]=="success": print("200",end="")\n');p.chmod(0o700)
   source=root/'compose.json';source.write_text(json.dumps({'services':{'api':{'image':'old:tag'},'werss':{'image':'old:tag'}}}))
   caddy=root/'Caddyfile';caddy.write_text('existing.seewiki.net { respond "ok" }\n')
   env={**os.environ,'PATH':str(b)+':'+os.environ['PATH'],'CALL_LOG':str(root/'calls'),'CASE':'success','ACR_REGISTRY':'example.test','ACR_NAMESPACE':'team','ACR_USERNAME':'test','ACR_PASSWORD':'test','PROJECT_NAME':'shared','COMPOSE_FILE':str(source),'LOCAL_HEALTH_URL':'http://example/health','HEALTH_ATTEMPTS':'1','ECS_CADDYFILE':str(caddy)}
   def release(owner,service,routes):
    r=subprocess.run(['bash',str(SCRIPT),'v1'],env={**env,**({'ECS_ROUTE_OWNER':owner} if owner is not None else {}),'DEPLOY_SERVICES_JSON':json.dumps({service:service}),'DOMAIN_ROUTES_JSON':json.dumps(routes)},capture_output=True,text=True)
    self.assertEqual(r.returncode,0,r.stderr)
   release('spider','api',{'spider.seewiki.net':3300})
   release('werss','werss',{'werss.seewiki.net':3384})
   self.assertIn('spider.seewiki.net',caddy.read_text())
   self.assertIn('werss.seewiki.net',caddy.read_text())
   release('werss','werss',{})
   self.assertIn('spider.seewiki.net',caddy.read_text())
   self.assertNotIn('werss.seewiki.net',caddy.read_text())
   self.assertIn('existing.seewiki.net',caddy.read_text())
   release(None,'api',{'legacy.seewiki.net':3300})
   self.assertIn('# BEGIN ECS ROUTES shared',caddy.read_text())
   self.assertIn('# BEGIN ECS ROUTES spider',caddy.read_text())
   for owner in ['', 'UPPER', 'a b', '-bad', 'a\nb', '$(id)']:
    r=subprocess.run(['bash',str(SCRIPT),'v1'],env={**env,'ECS_ROUTE_OWNER':owner,'DEPLOY_SERVICES_JSON':'{}'},capture_output=True,text=True)
    self.assertNotEqual(r.returncode,0)
    self.assertIn('Invalid ECS_ROUTE_OWNER',r.stderr)
if __name__=='__main__':unittest.main()
