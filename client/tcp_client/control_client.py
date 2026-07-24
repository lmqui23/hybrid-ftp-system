import os
import sys
import socket

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from common.ftp_shared import (
    udp_client_receive_buffer,
    udp_client_receive_file,
    udp_client_send_file
)

DEFAULT_PORT = 2121
DEFAULT_IP = "127.0.0.1"
BUFFER_SIZE = 4096


class FTPClient:
    def __init__(self):
        self.tcp_sock: socket.socket | None = None
        self.connected: bool = False

    def _receive_response(self) -> str:
        if not self.tcp_sock:
            return ""
        try:
            data = self.tcp_sock.recv(BUFFER_SIZE)
            if not data:
                self.connected = False
                return ""
            return data.decode('utf-8', errors='ignore')
        except Exception:
            self.connected = False
            return ""

    def _send_command(self, cmd: str) -> bool:
        if not self.tcp_sock:
            return False
        full_cmd = cmd + "\r\n"
        try:
            bytes_sent = self.tcp_sock.send(full_cmd.encode('utf-8'))
            return bytes_sent > 0
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
        if not self._send_command(cmd_line):
            print("[Error] Failed to send command to server.")
            return

        response = self._receive_response()
        print(response, end="")

        code = self._get_response_code(response)
        if code == 150:
            parts = cmd_line.split(' ', 1)
            cmd = parts[0].upper()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("LIST", "NLST"):
                data = udp_client_receive_buffer()
                if data:
                    print(data.decode('utf-8', errors='ignore'))
            elif cmd == "RETR":
                filename = arg if arg else "downloaded_file"
                udp_client_receive_file(filename)
            elif cmd in ("STOR", "APPE", "STOU"):
                udp_client_send_file(arg)

            final_res = self._receive_response()
            print(final_res, end="")

    def connect_to_server(self, ip: str, port: int) -> bool:
        try:
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.connect((ip, port))
            self.connected = True

            welcome_msg = self._receive_response()
            print(welcome_msg, end="")
            return True
        except Exception as e:
            print(f"[Error] Connection to server failed: {e}")
            if self.tcp_sock:
                self.tcp_sock.close()
            return False

    def disconnect(self) -> None:
        if self.connected:
            self._send_command("QUIT")
            print(self._receive_response(), end="")
            if self.tcp_sock:
                self.tcp_sock.close()
            self.connected = False

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

                if cmd in ("EXIT", "QUIT"):
                    self.disconnect()
                    break

                if cmd in ("LIST", "NLST", "RETR", "STOR", "APPE", "STOU"):
                    self.handle_data_transfer(user_input)
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