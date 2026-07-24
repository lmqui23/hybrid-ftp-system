import os
import sys
import socket
import re
import threading
from pathlib import Path

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from protocol.file_hash import sha256_file
from common.ftp_shared import (
    udp_client_receive_buffer,
    udp_client_receive_file,
    udp_client_send_file,
    udp_client_prepare_active,
    udp_client_set_passive,
    udp_client_abort_transfer,
)

DEFAULT_PORT = 2121
DEFAULT_IP = "127.0.0.1"
BUFFER_SIZE = 4096


class FTPClient:
    def __init__(self):
        self.tcp_sock: socket.socket | None = None
        self.connected: bool = False
        self.data_mode: str | None = None
        self.reader = None
        self.transfer_thread: threading.Thread | None = None

    @staticmethod
    def _parse_transfer_metadata(response: str):
        values = dict(re.findall(r"(TID|SIZE|SHA256)=([^\s]+)", response))
        try:
            return int(values["TID"]), int(values["SIZE"]), values["SHA256"]
        except (KeyError, ValueError):
            return None

    def _enter_passive(self) -> bool:
        if not self._send_command("PASV"):
            return False
        response = self._receive_response()
        print(response, end="")
        match = re.search(
            r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)",
            response,
        )
        if self._get_response_code(response) != 227 or match is None:
            return False
        numbers = [int(value) for value in match.groups()]
        ip = ".".join(str(value) for value in numbers[:4])
        port = numbers[4] * 256 + numbers[5]
        udp_client_set_passive(ip, port)
        self.data_mode = "PASV"
        return True

    def _enter_active(self) -> bool:
        if self.tcp_sock is None:
            return False
        local_ip = self.tcp_sock.getsockname()[0]
        ip, port = udp_client_prepare_active(local_ip)
        values = ip.split(".") + [str(port // 256), str(port % 256)]
        if not self._send_command("PORT " + ",".join(values)):
            return False
        response = self._receive_response()
        print(response, end="")
        if self._get_response_code(response) != 200:
            return False
        self.data_mode = "PORT"
        return True

    @staticmethod
    def _upload_path(argument: str) -> Path:
        direct = Path(argument)
        if direct.is_file():
            return direct
        project_root = Path(__file__).resolve().parents[2]
        return project_root / "storage" / "client_files" / argument

    def _receive_response(self) -> str:
        if self.reader is None:
            return ""
        try:
            first = self.reader.readline()
            if not first:
                self.connected = False
                return ""
            response = first
            if len(first) >= 4 and first[:3].isdigit() and first[3] == "-":
                final_prefix = first[:3] + " "
                while True:
                    line = self.reader.readline()
                    if not line:
                        break
                    response += line
                    if line.startswith(final_prefix):
                        break
            return response
        except Exception:
            self.connected = False
            return ""

    def _send_command(self, cmd: str) -> bool:
        if not self.tcp_sock:
            return False
        full_cmd = cmd + "\r\n"
        try:
            self.tcp_sock.sendall(full_cmd.encode('utf-8'))
            return True
        except Exception:
            return False

    @staticmethod
    def _get_response_code(response: str) -> int:
        if len(response) < 3:
            return -1
        try:
            return int(response[:3])
        except ValueError:
            return -1

    def handle_data_transfer(self, cmd_line: str) -> None:
        parts = cmd_line.split(" ", 1)
        cmd = parts[0].upper()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if self.data_mode is None and not self._enter_passive():
            print("[Error] Cannot prepare UDP data channel.")
            return

        upload_path = None
        command_to_send = cmd_line
        if cmd in ("STOR", "APPE", "STOU"):
            upload_path = self._upload_path(arg)
            if not upload_path.is_file():
                print(f"[Error] Local file not found: {upload_path}")
                return
            size = upload_path.stat().st_size
            file_hash = sha256_file(upload_path)
            if cmd == "STOU":
                command_to_send = (
                    f"STOU SIZE={size} SHA256={file_hash}"
                )
            else:
                command_to_send = (
                    f"{cmd} {upload_path.name} "
                    f"SIZE={size} SHA256={file_hash}"
                )

        if not self._send_command(command_to_send):
            print("[Error] Failed to send command to server.")
            return

        response = self._receive_response()
        print(response, end="")

        code = self._get_response_code(response)
        if code == 150:
            metadata = self._parse_transfer_metadata(response)
            if metadata is None:
                print("[Error] Missing transfer metadata.")
                return
            transfer_id, expected_size, expected_hash = metadata

            if cmd in ("LIST", "NLST"):
                data = udp_client_receive_buffer(
                    transfer_id,
                    expected_size,
                    expected_hash,
                )
                if data:
                    print(data.decode('utf-8', errors='ignore'))
            elif cmd == "RETR":
                project_root = Path(__file__).resolve().parents[2]
                save_path = (
                    project_root
                    / "storage"
                    / "client_files"
                    / (Path(arg).name or "downloaded_file")
                )
                udp_client_receive_file(
                    str(save_path),
                    transfer_id,
                    expected_size,
                    expected_hash,
                )
            elif cmd in ("STOR", "APPE", "STOU"):
                udp_client_send_file(str(upload_path), transfer_id)

            final_res = self._receive_response()
            print(final_res, end="")
            self.data_mode = None

    def connect_to_server(self, ip: str, port: int) -> bool:
        try:
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.connect((ip, port))
            self.reader = self.tcp_sock.makefile(
                "r",
                encoding="utf-8",
                errors="ignore",
                newline="",
            )
            self.connected = True

            welcome_msg = self._receive_response()
            print(welcome_msg, end="")
            return True
        except Exception as e:
            print(f"[Error] Connection to server failed: {e}")
            if self.tcp_sock:
                self.tcp_sock.close()
            if self.reader:
                self.reader.close()
                self.reader = None
            return False

    def disconnect(self) -> None:
        if self.connected:
            if self.transfer_thread and self.transfer_thread.is_alive():
                self._send_command("ABOR")
                udp_client_abort_transfer()
                self.transfer_thread.join(timeout=3)
            self._send_command("QUIT")
            print(self._receive_response(), end="")
            if self.reader:
                self.reader.close()
                self.reader = None
            if self.tcp_sock:
                self.tcp_sock.close()
            self.connected = False

    def _run_transfer(self, command: str) -> None:
        try:
            self.handle_data_transfer(command)
        finally:
            self.transfer_thread = None

    def run_cli(self) -> None:
        print("====================================================")
        print("   HYBRID FTP CLIENT CLI - READY FOR COMMANDS       ")
        print("====================================================")
        print(" [Auth/Session]: USER, PASS, QUIT")
        print(" [Data Mode]   : PASV, PORT, TYPE")
        print(" [Directory]   : PWD, CWD, CDUP, MKD, RMD")
        print(" [File/Data]   : LIST, NLST, RETR, STOR, APPE, STOU, DELE, RNFR, RNTO, ABOR")
        print(" [Info/Meta]   : STAT, SIZE, MDTM, HASH")
        print("====================================================\n")

        try:
            while self.connected:
                user_input = input("ftp> ").strip()
                if not user_input:
                    continue

                parts = user_input.split()
                cmd = parts[0].upper()

                transfer_running = (
                    self.transfer_thread is not None
                    and self.transfer_thread.is_alive()
                )

                if cmd == "ABOR" and transfer_running:
                    if self._send_command("ABOR"):
                        udp_client_abort_transfer()
                        print("[Client] Abort requested.")
                    continue

                if transfer_running:
                    print("[Client] Transfer in progress. Use ABOR or wait.")
                    continue

                if cmd in ("EXIT", "QUIT"):
                    self.disconnect()
                    break

                if cmd == "PASV":
                    self._enter_passive()
                elif cmd == "PORT":
                    self._enter_active()
                elif cmd in ("LIST", "NLST", "RETR", "STOR", "APPE", "STOU"):
                    self.transfer_thread = threading.Thread(
                        target=self._run_transfer,
                        args=(user_input,),
                        daemon=True,
                    )
                    self.transfer_thread.start()
                else:
                    if self._send_command(user_input):
                        response = self._receive_response()
                        if not response:
                            print("[Client] Server closed the connection.")
                            break
                        print(response, end="")
                    else:
                        print("[Error] Failed to send command!")
        except KeyboardInterrupt:
            print("\n[Client] Interrupted by user.")
            self.disconnect()


def main():
    ip = DEFAULT_IP
    port = DEFAULT_PORT

    if len(sys.argv) >= 2:
        ip = sys.argv[1]
    if len(sys.argv) >= 3:
        try:
            port = int(sys.argv[2])
        except ValueError:
            print(f"[Fatal] Invalid port number: {sys.argv[2]}")
            sys.exit(1)

    if port <= 0 or port > 65535:
        print(f"[Fatal] Invalid port number: {port}")
        sys.exit(1)

    client = FTPClient()
    print(f"[Client] Connecting to {ip}:{port}...")

    if client.connect_to_server(ip, port):
        client.run_cli()


if __name__ == "__main__":
    main()
