"""The deadline must be evaluated on ECS, not when constructing the command."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class RemoteDeadlineTests(unittest.TestCase):
    def test_command_uses_remote_clock_after_wrapper_entry(self):
        workflow=Path(__file__).parents[1]/'.github/workflows/deploy-ecs-reusable.yml'
        line=next(x.strip() for x in workflow.read_text().splitlines() if x.strip().startswith('command_content='))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'date').write_text('#!/bin/sh\necho "$CLOCK_VALUE"\n')
            (root/'bash').write_text('#!/bin/sh\necho "$ECS_RELEASE_DEADLINE_EPOCH"\n')
            for name in ['date','bash']: (root/name).chmod(0o700)
            # Expand the workflow string with GitHub's clock, then execute the
            # resulting command with the simulated ECS clock.
            code='export CLOCK_VALUE=1000\n'+line+'\nexport CLOCK_VALUE=2000\neval "$command_content"'
            result=subprocess.run(['/bin/bash','-c',code], env={**os.environ,'PATH':d+':'+os.environ['PATH']},text=True,capture_output=True,check=True)
            self.assertEqual(result.stdout.strip(),'2450')

if __name__=='__main__': unittest.main()
