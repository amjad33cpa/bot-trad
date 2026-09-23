import json
import os
import socket
import subprocess
import sys
import time
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError, URLError


class ServiceIntegration(unittest.TestCase):
    def test_setup_process_health_and_readiness(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, MODE="setup", PORT=str(port), PYTHONIOENCODING="utf-8")
        process = subprocess.Popen([sys.executable, "-m", "sentinel"], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            payload = None
            for _ in range(50):
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        payload = json.load(response)
                        break
                except URLError:
                    time.sleep(.1)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["service"], "setup")
            self.assertFalse(payload["alerts_enabled"])
            with self.assertRaises(HTTPError) as cm:
                urlopen(f"http://127.0.0.1:{port}/ready", timeout=1)
            self.assertEqual(cm.exception.code, 503)
            cm.exception.close()
        finally:
            process.terminate()
            process.wait(timeout=5)
