"""Exercise the workflow's actual manifest parser and remote command."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).parents[1] / '.github/workflows/deploy-ecs-reusable.yml'

class RouteOwnerWorkflowTests(unittest.TestCase):
    def test_manifest_default_explicit_and_invalid(self):
        lines = WORKFLOW.read_text().splitlines()
        validation = next(x.strip() for x in lines if "jq -e 'if has(\"route_owner\")" in x)
        output = next(x.strip() for x in lines if "printf 'route_owner='" in x)
        cases = [({}, 'guanbo-production'), ({'project_name': 'shared'}, 'shared'),
                 ({'project_name': 'shared', 'route_owner': 'werss-1'}, 'werss-1')]
        cases += [({'route_owner': v}, None) for v in ['', None, 42, [], 'UPPER', 'a b', 'a\nb', 'a\n', '$(id)', '-bad']]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'manifest.json'
            for manifest, expected in cases:
                with self.subTest(manifest=manifest):
                    config.write_text(json.dumps(manifest))
                    result = subprocess.run(['bash', '-ec', validation + '\n' + output],
                                            env={**os.environ, 'CONFIG': str(config)},
                                            text=True, capture_output=True)
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, '')
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, 'route_owner=' + expected + '\n')

    def test_remote_owner_is_shell_quoted(self):
        lines = WORKFLOW.read_text().splitlines()
        quote = next(x.strip() for x in lines if 'printf -v owner_q' in x)
        command = next(x.strip() for x in lines if x.strip().startswith('command_content='))
        self.assertIn('route_owner: ${{ steps.manifest.outputs.route_owner }}', WORKFLOW.read_text())
        self.assertIn('ECS_ROUTE_OWNER: ${{ needs.prepare.outputs.route_owner }}', WORKFLOW.read_text())
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / 'bash'
            stub.write_text('#!/bin/sh\nprintf "%s" "$ECS_ROUTE_OWNER"\n')
            stub.chmod(0o700)
            # Invalid manifest input is rejected above; even directly injected
            # shell syntax cannot execute while constructing the remote command.
            for owner in ['werss-1', 'bad; echo injected', '$(echo injected)']:
                result = subprocess.run(['/bin/bash', '-ec', quote + '\n' + command + '\neval "$command_content"'],
                                        env={**os.environ, 'ECS_ROUTE_OWNER': owner,
                                             'PATH': directory + ':' + os.environ['PATH']},
                                        text=True, capture_output=True, check=True)
                self.assertEqual(result.stdout, owner)

if __name__ == '__main__':
    unittest.main()
