from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from organism.lease import BodyLease, BodyLeaseBusy


class BodyLeaseTests(unittest.TestCase):
    def test_single_writer_is_process_safe_and_release_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            lease = BodyLease(Path(directory) / "body.lock")
            first = lease.acquire("navigation", "navigate")
            try:
                with self.assertRaises(BodyLeaseBusy):
                    lease.acquire("laboratory", "training")
            finally:
                first.release()
            second = lease.acquire("laboratory", "training")
            second.release()


if __name__ == "__main__":
    unittest.main()
