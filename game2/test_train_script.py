import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


class TrainScriptTests(unittest.TestCase):
    def run_script(self, fresh, auto=False):
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
                'else:\n'
                '    time.sleep(0.05)\n'
            )
            fake_python.chmod(0o755)
            env = dict(os.environ, PYTHON=str(fake_python), ARGUMENTS=str(arguments),
                       EPISODES='1', PORT='18765')
            if auto:
                env['AUTO_SPEED'] = '50'
            if fresh:
                env['FRESH'] = '1'
            else:
                env.pop('FRESH', None)
            command = ['bash', 'train.sh'] + (['auto'] if auto else [])
            result = subprocess.run(command, cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=3)
            return result, arguments.read_text().splitlines()

    def test_fresh_flag_and_training_mode_are_explicit(self):
        resume, resume_arguments = self.run_script(False)
        self.assertEqual(resume.returncode, 0)
        self.assertIn('training_mode=resume', resume.stderr)
        self.assertIn('clock_mode=realtime', resume.stderr)
        self.assertIn('renderer=window', resume.stderr)
        engine_arguments = next(line for line in resume_arguments if line.startswith('engine.py '))
        self.assertIn('--window', engine_arguments.split())
        self.assertNotIn('--auto', engine_arguments.split())
        self.assertTrue(any(line.startswith('mlp_runner.py ') for line in resume_arguments))
        self.assertFalse(any('--fresh' in line for line in resume_arguments))

        fresh, fresh_arguments = self.run_script(True)
        self.assertEqual(fresh.returncode, 0)
        self.assertIn('training_mode=fresh', fresh.stderr)
        runner_arguments = [line for line in fresh_arguments if line.startswith('mlp_runner.py ')]
        self.assertEqual(len(runner_arguments), 1)
        self.assertIn('--fresh', runner_arguments[0].split())

    def test_auto_mode_is_headless_and_passes_speed(self):
        result, arguments = self.run_script(False, auto=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('clock_mode=auto', result.stderr)
        self.assertIn('speed=50x', result.stderr)
        engine_arguments = next(line for line in arguments if line.startswith('engine.py '))
        self.assertIn('--auto', engine_arguments.split())
        self.assertIn('--speed 50', engine_arguments)
        self.assertNotIn('--window', engine_arguments.split())


if __name__ == '__main__':
    unittest.main()
