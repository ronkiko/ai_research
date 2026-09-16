import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


class TrainScriptTests(unittest.TestCase):
    def run_script(self, fresh):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fake_python = directory / 'fake-python'
            arguments = directory / 'arguments.log'
            fake_python.write_text(
                '#!/usr/bin/env python3\n'
                'import os, sys, time\n'
                'with open(os.environ["ARGUMENTS"], "a") as log:\n'
                '    log.write(" ".join(sys.argv[1:]) + "\\n")\n'
                'if sys.argv[1] == "engine.py":\n'
                '    time.sleep(10)\n'
            )
            fake_python.chmod(0o755)
            env = dict(os.environ, PYTHON=str(fake_python), ARGUMENTS=str(arguments),
                       EPISODES='1', PORT='18765')
            if fresh:
                env['FRESH'] = '1'
            else:
                env.pop('FRESH', None)
            result = subprocess.run(['bash', 'train.sh'], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=3)
            return result, arguments.read_text().splitlines()

    def test_fresh_flag_and_training_mode_are_explicit(self):
        resume, resume_arguments = self.run_script(False)
        self.assertEqual(resume.returncode, 0)
        self.assertIn('training_mode=resume', resume.stderr)
        self.assertTrue(any(line.startswith('mlp_runner.py ') for line in resume_arguments))
        self.assertFalse(any('--fresh' in line for line in resume_arguments))

        fresh, fresh_arguments = self.run_script(True)
        self.assertEqual(fresh.returncode, 0)
        self.assertIn('training_mode=fresh', fresh.stderr)
        runner_arguments = [line for line in fresh_arguments if line.startswith('mlp_runner.py ')]
        self.assertEqual(len(runner_arguments), 1)
        self.assertIn('--fresh', runner_arguments[0].split())


if __name__ == '__main__':
    unittest.main()
