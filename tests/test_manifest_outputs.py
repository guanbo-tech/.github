"""Run the exact workflow output line, checking GitHub key boundaries."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).parents[1] / '.github/workflows/deploy-ecs-reusable.yml'

class ManifestOutputsTests(unittest.TestCase):
    def test_optional_routes_do_not_consume_next_output(self):
        line = next(line.strip() for line in WORKFLOW.read_text().splitlines()
                    if "printf 'domain_routes=" in line)
        for routes, expected in [(None, ''), ({}, '{}'), ({'api.seewiki.net': 3400}, '{"api.seewiki.net":3400}')]:
            with self.subTest(routes=routes), tempfile.TemporaryDirectory() as directory:
                manifest = {'services': [{'name': 'api'}]}
                if routes is not None:
                    manifest['domain_routes'] = routes
                path = Path(directory) / 'manifest.json'
                path.write_text(json.dumps(manifest))
                result = subprocess.run(['bash', '-c', line + '\nprintf "services=next\\n"'],
                                        env={**os.environ, 'CONFIG': str(path)},
                                        text=True, capture_output=True, check=True)
                self.assertEqual(result.stdout.splitlines(), ['domain_routes=' + expected, 'services=next'])

if __name__ == '__main__': unittest.main()
