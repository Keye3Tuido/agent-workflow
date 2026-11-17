import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

try:
    from mcp import Client
    from coordination import service_control
except ImportError:
    Client = None
    service_control = None


@unittest.skipUnless(Client is not None, "需要安装 mcp==2.3.0")
class ServiceControlExperiments(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name) / "state"

    def tearDown(self):
        if service_control is None:
            return
        try:
            if service_control.status(self.state)["running"]:
                service_control.stop(self.state)
        except Exception:
            pass
        for process in list(service_control._CHILDREN.values()):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        service_control._CHILDREN.clear()
        self.temporary.cleanup()

    def test_ensure_reuses_singleton_and_stop_recovers_stale_endpoint(self):
        first = service_control.ensure(self.state)
        self.assertEqual(first["action"], "started")
        first_endpoint = first["endpoint"]
        second = service_control.ensure(self.state)
        self.assertEqual(second["action"], "existing")
        self.assertEqual(second["endpoint"]["instance_id"], first_endpoint["instance_id"])
        self.assertEqual(second["endpoint"]["pid"], first_endpoint["pid"])
        self.assertTrue(service_control.status(self.state)["running"])

        stopped = service_control.stop(self.state)
        self.assertTrue(stopped["stopped"])
        self.assertFalse(service_control.status(self.state)["running"])
        (self.state / "endpoint.json").write_text("stale\n", encoding="utf-8")
        replacement = service_control.ensure(self.state)
        self.assertEqual(replacement["action"], "started")
        self.assertNotEqual(
            replacement["endpoint"]["instance_id"], first_endpoint["instance_id"]
        )
        self.assertTrue(service_control.status(self.state)["running"])
        service_control.stop(self.state)


if __name__ == "__main__":
    unittest.main()
