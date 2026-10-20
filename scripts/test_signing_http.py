"""Run the workflow's exact signing shell against a fake local HTTP service.

python scripts/test_signing_http.py (bash and curl required; Git Bash works on Windows).
No checkout, hardware signing, release attachment or publication is performed.
"""
import http.server
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
KEY = "fake-signing-test-key"


class SigningHttpTests(unittest.TestCase):
    def test_request_and_failure_boundaries(self):
        text = (ROOT / ".github/workflows/publish-mcp.yml").read_text()
        block = text.split("    - name: Sign it\n", 1)[1].split("    - name: Verify the signature", 1)[0]
        shell = "\n".join(line[8:] for line in block.split("      run: |\n", 1)[1].splitlines())
        requests = []
        state = {"status": 200}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append((self.headers.get("Authorization"), body))
                self.send_response(state["status"])
                self.end_headers()
                self.wfile.write(b"signed-output" if state["status"] == 200 else b"refused")

            def log_message(self, *args):
                pass

        bash = shutil.which("bash")
        git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if os.name == "nt" and git_bash.exists():
            bash = str(git_bash)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for key, status in [("", 200), ("   ", 200), (KEY, 401), (KEY, 200)]:
                with self.subTest(key_present=bool(key.strip()), status=status):
                    state["status"] = status
                    before = len(requests)
                    with tempfile.TemporaryDirectory() as folder:
                        path = Path(folder)
                        (path / "unsigned").mkdir()
                        original = path / "unsigned/mcp-windbg.exe"
                        original.write_bytes(b"original-binary")
                        env = dict(os.environ, SIGNING_API_KEY=key,
                                   SIGNING_URL=f"http://127.0.0.1:{server.server_port}/sign")
                        result = subprocess.run([bash, "-c", shell], cwd=path, env=env,
                                                capture_output=True, text=True)
                        success = bool(key.strip()) and status == 200
                        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
                        self.assertEqual(original.read_bytes(), b"original-binary")
                        signed = path / "signed/mcp-windbg.exe"
                        self.assertEqual(signed.exists(), success)
                        if success:
                            self.assertEqual(signed.read_bytes(), b"signed-output")
                        self.assertFalse((path / "signed/mcp-windbg.exe.pending").exists())
                        self.assertEqual(len(requests) - before, 1 if key.strip() else 0)
                        self.assertNotIn(KEY, result.stdout + result.stderr)
                        if key.strip():
                            self.assertEqual(requests[-1][0], f"Bearer {KEY}")
                            self.assertIn(b'name="File"', requests[-1][1])
                            self.assertIn(b"original-binary", requests[-1][1])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
