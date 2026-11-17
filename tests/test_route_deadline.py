"""The readiness loop uses one deadline across all declared domains."""
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

class DeadlineTests(unittest.TestCase):
    def test_many_failing_domains_share_remaining_budget(self):
        script = (Path(__file__).parents[1] / 'scripts/deploy.sh').read_text()
        code = script.split("<<'PYROUTES'\n", 1)[1].split('\nPYROUTES', 1)[0]
        now = [0.0]
        calls = []
        def run(args, **kwargs):
            duration = float(args[args.index('--max-time') + 1])
            calls.append(duration)
            now[0] += duration
            return subprocess.CompletedProcess(args, 7, stdout='000')
        with patch.dict(os.environ, {'ROUTE_TIMEOUT_SECONDS': '12', 'DOMAIN_ROUTES_JSON':
                                    '{"a.seewiki.net":1,"b.seewiki.net":2,"c.seewiki.net":3}'}), \
             patch('time.monotonic', side_effect=lambda: now[0]), \
             patch('time.sleep', side_effect=lambda value: now.__setitem__(0, now[0] + value)), \
             patch('subprocess.run', side_effect=run):
            with self.assertRaisesRegex(SystemExit, 'shared readiness deadline'):
                exec(compile(code, '<readiness>', 'exec'), {})
        self.assertEqual(calls, [10.0, 2.0])
        self.assertEqual(now[0], 12.0)

if __name__ == '__main__': unittest.main()
