import importlib.util
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('routes',Path(__file__).parents[1]/'scripts/domain-routes.py')
routes=importlib.util.module_from_spec(spec);spec.loader.exec_module(routes)
class RoutesTests(unittest.TestCase):
    def test_preserves_foreign_rules_and_replaces_owned_block(self):
        base='admin.seewiki.net {\n respond "restricted" 403\n}\n'
        first=routes.render(base,'koubo',{'koubo-api.seewiki.net':3400})
        second=routes.render(first,'koubo',{'koubo-worker.seewiki.net':3410})
        self.assertIn(base,second)
        self.assertNotIn('koubo-api.seewiki.net',second)
        self.assertIn('reverse_proxy 127.0.0.1:3410',second)
    def test_rejects_conflicts_and_injection(self):
        for base,domain,port in [('', 'evil.net',3400),('', 'x.seewiki.net\n}',3400),('', 'x.seewiki.net','3400'),('', 'x.seewiki.net',True),('x.seewiki.net {}','x.seewiki.net',3400),('*.seewiki.net {}','x.seewiki.net',3400)]:
            with self.subTest(domain=domain,port=port):
                with self.assertRaises(ValueError): routes.render(base,'koubo',{domain:port})
    def test_does_not_steal_other_owner(self):
        text=routes.render('', 'other', {'x.seewiki.net':3000})
        with self.assertRaises(ValueError): routes.render(text,'koubo',{'x.seewiki.net':3400})
    def test_write_preserves_bind_mount_inode(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'Caddyfile';p.write_text('previous long content')
            inode=p.stat().st_ino
            routes.write_in_place(p,b'new')
            self.assertEqual(p.stat().st_ino,inode)
            self.assertEqual(p.read_bytes(),b'new')
    def test_malformed_markers_rejected(self):
        with self.assertRaises(ValueError): routes.render('# BEGIN ECS ROUTES koubo\n','koubo',{})
if __name__=='__main__': unittest.main()
