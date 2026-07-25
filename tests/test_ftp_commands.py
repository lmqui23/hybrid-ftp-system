import hashlib
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_STORAGE = ROOT / "storage" / "server_files"


def free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class RawFTPClient:
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), 2)
        self.reader = self.sock.makefile("rb")
        self.assert_code(220)

    def command(self, command: str) -> tuple[int, str]:
        self.sock.sendall((command + "\r\n").encode())
        line = self.reader.readline().decode("utf-8", errors="replace").rstrip()
        return int(line[:3]), line

    def assert_code(self, expected: int, command: str | None = None) -> str:
        if command is None:
            line = self.reader.readline().decode("utf-8", errors="replace").rstrip()
            code = int(line[:3])
        else:
            code, line = self.command(command)
        if code != expected:
            raise AssertionError(f"{command!r}: expected {expected}, received {line!r}")
        return line

    def login(self) -> None:
        self.assert_code(331, "USER admin")
        self.assert_code(230, "PASS 123456")

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.sock.close()


class FTPCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SERVER_STORAGE.mkdir(parents=True, exist_ok=True)
        cls.port = free_tcp_port()
        environment = os.environ.copy()
        environment["FTP_CONTROL_PORT"] = str(cls.port)
        cls.server = subprocess.Popen(
            [sys.executable, "server/tcp_server.py"],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                probe = socket.create_connection(("127.0.0.1", cls.port), 0.1)
                probe.close()
                return
            except OSError:
                time.sleep(0.05)
        cls.server.terminate()
        raise RuntimeError("FTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.server.kill()

    def connect(self, login: bool = True) -> RawFTPClient:
        client = RawFTPClient(self.port)
        if login:
            client.login()
        self.addCleanup(client.close)
        return client

    def test_authentication_session_and_unknown_command(self):
        client = self.connect(login=False)
        client.assert_code(530, "PWD")
        client.assert_code(503, "PASS 123456")
        client.assert_code(331, "USER nobody")
        client.assert_code(530, "PASS wrong")
        client.assert_code(331, "USER admin")
        client.assert_code(230, "PASS 123456")
        client.assert_code(200, "NOOP")
        client.assert_code(502, "UNKNOWN")
        client.assert_code(221, "QUIT")

    def test_type_mode_port_pasv_and_help(self):
        client = self.connect()
        client.assert_code(200, "TYPE A")
        client.assert_code(200, "TYPE I")
        client.assert_code(504, "TYPE X")
        client.assert_code(200, "MODE S")
        client.assert_code(504, "MODE X")
        client.assert_code(501, "PORT 127,0,0,1,1")
        client.assert_code(501, "PORT 999,0,0,1,8,1")
        client.assert_code(501, "PORT 127,0,0,1,0,20")
        client.assert_code(200, "PORT 127,0,0,1,8,1")
        client.assert_code(227, "PASV")
        client.assert_code(214, "HELP RETR")

    def test_directory_tree_and_path_safety(self):
        client = self.connect()
        name = f"commands_{os.getpid()}_{time.time_ns()}"
        base = SERVER_STORAGE / name

        try:
            client.assert_code(257, "PWD")
            client.assert_code(501, "MKD")
            client.assert_code(257, f"MKD {name}")
            client.assert_code(550, f"MKD {name}")
            client.assert_code(250, f"CWD {name}")
            self.assertIn(name, client.assert_code(257, "PWD"))
            client.assert_code(257, "MKD child")
            client.assert_code(250, "CWD child")
            client.assert_code(250, "CDUP")
            client.assert_code(250, "RMD child")
            client.assert_code(550, "CWD missing")
            client.assert_code(550, "CWD ../../..")
            client.assert_code(250, "CDUP")
            client.assert_code(250, "CDUP")
            client.assert_code(550, "RMD /")
            client.assert_code(250, f"RMD {name}")
        finally:
            if base.exists():
                for path in sorted(base.rglob("*"), reverse=True):
                    path.rmdir() if path.is_dir() else path.unlink()
                base.rmdir()

    def test_file_metadata_rename_and_delete(self):
        client = self.connect()
        name = f"metadata_{os.getpid()}_{time.time_ns()}.bin"
        renamed = f"renamed_{os.getpid()}_{time.time_ns()}.bin"
        path = SERVER_STORAGE / name
        renamed_path = SERVER_STORAGE / renamed
        content = b"\x00binary\r\ncontent\xff"
        path.write_bytes(content)

        try:
            self.assertEqual(client.assert_code(213, f"SIZE {name}"), f"213 {len(content)}")
            self.assertRegex(client.assert_code(213, f"MDTM {name}"), r"^213 \d{14}$")
            expected = hashlib.sha256(content).hexdigest()
            self.assertIn(expected, client.assert_code(200, f"HASH {name}"))
            client.assert_code(550, "SIZE missing")
            client.assert_code(550, "MDTM missing")
            client.assert_code(550, "HASH missing")
            client.assert_code(503, f"RNTO {renamed}")
            client.assert_code(350, f"RNFR {name}")
            client.assert_code(250, f"RNTO {renamed}")
            self.assertTrue(renamed_path.exists())
            client.assert_code(250, f"DELE {renamed}")
            self.assertFalse(renamed_path.exists())
            client.assert_code(550, f"DELE {renamed}")
        finally:
            path.unlink(missing_ok=True)
            renamed_path.unlink(missing_ok=True)

    def test_status_commands(self):
        client = self.connect()
        self.assertTrue(client.assert_code(211, "STAT").startswith("211 "))

        # STAT replies contain multiple status lines, so use a separate session.
        status_client = self.connect()
        name = f"stat_{os.getpid()}_{time.time_ns()}.txt"
        path = SERVER_STORAGE / name
        path.write_text("status", encoding="utf-8")
        try:
            self.assertTrue(
                status_client.assert_code(213, f"STAT {name}").startswith("213 ")
            )
        finally:
            path.unlink(missing_ok=True)

    def test_data_commands_validate_mode_metadata_and_paths(self):
        client = self.connect()
        client.assert_code(425, "LIST")
        client.assert_code(425, "NLST")
        client.assert_code(425, "RETR missing")
        client.assert_code(425, "STOR upload.bin SIZE=1 SHA256=" + "0" * 64)

        client.assert_code(227, "PASV")
        client.assert_code(550, "RETR missing")
        client.assert_code(227, "PASV")
        client.assert_code(501, "STOR upload.bin")
        client.assert_code(227, "PASV")
        client.assert_code(501, "APPE upload.bin SIZE=-1 SHA256=" + "0" * 64)
        client.assert_code(227, "PASV")
        client.assert_code(501, "STOU SIZE=1 SHA256=bad")


if __name__ == "__main__":
    unittest.main()
