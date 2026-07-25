import hashlib
import io
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from client.control_client import FTPClient
from common.data_transfer import udp_client_abort_transfer


ROOT = Path(__file__).resolve().parents[1]
CLIENT_STORAGE = ROOT / "storage" / "client_files"
SERVER_STORAGE = ROOT / "storage" / "server_files"


def free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class FTPIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
                break
            except OSError:
                time.sleep(0.05)
        else:
            cls.server.terminate()
            raise RuntimeError("FTP test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.server.kill()

    def login(self) -> FTPClient:
        client = FTPClient()
        self.assertTrue(client.connect_to_server("127.0.0.1", self.port))
        self.assertTrue(client._send_command("USER admin"))
        self.assertEqual(client._get_response_code(client._receive_response()), 331)
        self.assertTrue(client._send_command("PASS 123456"))
        self.assertEqual(client._get_response_code(client._receive_response()), 230)
        return client

    def test_passive_active_commands_and_integrity(self):
        CLIENT_STORAGE.mkdir(parents=True, exist_ok=True)
        SERVER_STORAGE.mkdir(parents=True, exist_ok=True)
        name = f"integration_{os.getpid()}.bin"
        client_path = CLIENT_STORAGE / name
        server_path = SERVER_STORAGE / name
        content = bytes(range(256)) * 32
        expected_hash = hashlib.sha256(content).hexdigest()
        before_stou = set(SERVER_STORAGE.glob("stou_*"))
        clients = []

        try:
            client_path.write_bytes(content)
            client = self.login()
            clients.append(client)

            client.handle_data_transfer(f"STOR {name}")
            self.assertEqual(server_path.read_bytes(), content)

            client_path.unlink()
            self.assertTrue(client._enter_active())
            client.handle_data_transfer(f"RETR {name}")
            self.assertEqual(hashlib.sha256(client_path.read_bytes()).hexdigest(), expected_hash)

            client.handle_data_transfer("NLST")
            output = io.StringIO()
            with redirect_stdout(output):
                client.handle_data_transfer("LIST")
            self.assertIn(name, output.getvalue())

            client.handle_data_transfer(f"APPE {name}")
            self.assertEqual(server_path.read_bytes(), content + content)

            client.handle_data_transfer(f"STOU {name}")
            new_stou = set(SERVER_STORAGE.glob("stou_*")) - before_stou
            self.assertEqual(len(new_stou), 1)
            self.assertEqual(next(iter(new_stou)).read_bytes(), content)

            self.assertTrue(client._send_command("HELP"))
            help_response = client._receive_response()
            self.assertTrue(help_response.startswith("214-"))
            self.assertIn("214 End", help_response)

            client.tcp_sock.sendall(b"NOOP\r\nPWD\r\n")
            self.assertEqual(client._get_response_code(client._receive_response()), 200)
            self.assertEqual(client._get_response_code(client._receive_response()), 257)

            second = self.login()
            clients.append(second)
            self.assertTrue(second._send_command("NOOP"))
            self.assertEqual(second._get_response_code(second._receive_response()), 200)

            self.assertTrue(client._send_command("ABOR"))
            self.assertEqual(client._get_response_code(client._receive_response()), 225)
        finally:
            for client in clients:
                if client.connected:
                    client.disconnect()
            client_path.unlink(missing_ok=True)
            server_path.unlink(missing_ok=True)
            for path in set(SERVER_STORAGE.glob("stou_*")) - before_stou:
                path.unlink(missing_ok=True)

    def test_abor_cancels_live_download(self):
        name = f"abort_{os.getpid()}.bin"
        server_path = SERVER_STORAGE / name
        client_path = CLIENT_STORAGE / name
        server_path.write_bytes(bytes(range(256)) * 32768)
        client = self.login()

        try:
            worker = threading.Thread(
                target=client.handle_data_transfer,
                args=(f"RETR {name}",),
            )
            worker.start()
            time.sleep(0.05)
            self.assertTrue(client._send_command("ABOR"))
            udp_client_abort_transfer()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertFalse(client_path.exists())
            self.assertEqual(
                list(CLIENT_STORAGE.glob(f"{name}.part.*")),
                [],
            )
        finally:
            if client.connected:
                client.disconnect()
            server_path.unlink(missing_ok=True)
            client_path.unlink(missing_ok=True)

    def test_abor_cancels_live_upload(self):
        name = f"abort_upload_{os.getpid()}.bin"
        client_path = CLIENT_STORAGE / name
        server_path = SERVER_STORAGE / name
        client_path.write_bytes(bytes(range(256)) * 32768)
        client = self.login()

        try:
            worker = threading.Thread(
                target=client.handle_data_transfer,
                args=(f"STOR {name}",),
            )
            worker.start()
            time.sleep(0.05)
            self.assertTrue(client._send_command("ABOR"))
            udp_client_abort_transfer()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertFalse(server_path.exists())
            self.assertEqual(
                list(SERVER_STORAGE.glob(f"{name}.part.*")),
                [],
            )
        finally:
            if client.connected:
                client.disconnect()
            client_path.unlink(missing_ok=True)
            server_path.unlink(missing_ok=True)
            for part in SERVER_STORAGE.glob(f"{name}.part.*"):
                part.unlink(missing_ok=True)

    def test_two_clients_transfer_concurrently(self):
        names = [
            f"concurrent_{os.getpid()}_a.bin",
            f"concurrent_{os.getpid()}_b.bin",
        ]
        contents = [
            bytes(range(256)) * 4096,
            bytes(reversed(range(256))) * 4096,
        ]
        processes = []

        try:
            for name, content in zip(names, contents):
                (SERVER_STORAGE / name).write_bytes(content)
                (CLIENT_STORAGE / name).unlink(missing_ok=True)

            script = (
                "import sys;"
                "from client.control_client import FTPClient;"
                "c=FTPClient();"
                "assert c.connect_to_server('127.0.0.1',int(sys.argv[1]));"
                "assert c._send_command('USER admin');c._receive_response();"
                "assert c._send_command('PASS 123456');c._receive_response();"
                "c.handle_data_transfer('RETR '+sys.argv[2]);"
                "c.disconnect()"
            )
            for name in names:
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", script, str(self.port), name],
                        cwd=ROOT,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                )

            for process in processes:
                _, stderr = process.communicate(timeout=15)
                self.assertEqual(
                    process.returncode,
                    0,
                    stderr.decode("utf-8", errors="replace"),
                )

            for name, content in zip(names, contents):
                self.assertEqual((CLIENT_STORAGE / name).read_bytes(), content)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
            for name in names:
                (SERVER_STORAGE / name).unlink(missing_ok=True)
                (CLIENT_STORAGE / name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
