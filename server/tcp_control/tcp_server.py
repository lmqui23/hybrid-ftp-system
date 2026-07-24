import os
import sys
import socket
import datetime
import threading

# Thêm đường dẫn gốc của dự án và thư mục hiện tại vào sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Import các module theo đúng cấu trúc thư mục
import file_system
from common.ftp_shared import (
    FTPSession,
    DataMode,
    TransferType,
    udp_prepare_passive_listener,
    udp_set_active_target,
    udp_send_buffer,
    udp_send_file,
    udp_receive_file,
    udp_abort_transfer
)

CONTROL_PORT = 2121
BUFFER_SIZE = 1024

def get_current_timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_server(level: str, client_fd: int, message: str) -> None:
    print(f"[{get_current_timestamp()}] [{level}] [FD {client_fd}] {message}")

def send_reply(client_sock: socket.socket, client_fd: int, code: int, message: str) -> None:
    response = f"{code} {message}\r\n".encode('utf-8')
    try:
        client_sock.sendall(response)
    except Exception as e:
        log_server("ERROR", client_fd, f"Failed to send response or client disconnected: {e}")

def get_ftp_path(full_path: str, root_dir: str) -> str:
    if full_path == root_dir:
        return "/"
    if full_path.startswith(root_dir):
        rel = full_path[len(root_dir):]
        return rel if rel else "/"
    return "/"

def resolve_safe_path(base_dir: str, user_input: str, root_dir: str) -> tuple[bool, str]:
    if not user_input:
        target = base_dir
    elif user_input.startswith('/'):
        target = os.path.join(root_dir, user_input.lstrip('/'))
    else:
        target = os.path.join(base_dir, user_input)

    try:
        if os.path.exists(target):
            out_abs_path = os.path.realpath(target)
        else:
            parent_dir = os.path.dirname(target)
            filename = os.path.basename(target)

            if not parent_dir or not os.path.exists(parent_dir):
                return False, ""

            out_abs_path = os.path.join(os.path.realpath(parent_dir), filename)

        real_root = os.path.realpath(root_dir)
        if len(out_abs_path) < len(real_root) or not out_abs_path.startswith(real_root):
            return False, ""

        if len(out_abs_path) > len(real_root) and out_abs_path[len(real_root)] != os.sep:
            return False, ""

        return True, out_abs_path
    except Exception:
        return False, ""

def get_local_ip(client_sock: socket.socket) -> str:
    try:
        ip = client_sock.getsockname()[0]
        return ip if ip != "0.0.0.0" else "127.0.0.1"
    except Exception:
        return "127.0.0.1"

def handle_client_session(client_sock: socket.socket, client_ip: str, client_port: int) -> None:
    client_fd = client_sock.fileno()
    session = FTPSession(client_fd, client_ip, client_port)

    # Đặt root_dir trỏ mặc định vào storage/server_files
    storage_server = os.path.abspath(os.path.join(PROJECT_ROOT, "storage", "server_files"))
    if os.path.exists(storage_server):
        session.root_dir = storage_server
        session.current_dir = storage_server

    log_server("INFO", client_fd, f"Session initialized for {client_ip}:{client_port}")
    send_reply(client_sock, client_fd, 220, "Welcome to RDT-FTP Server (Hybrid TCP/UDP)")

    try:
        while True:
            data = client_sock.recv(BUFFER_SIZE)
            if not data:
                log_server("INFO", client_fd, "Client disconnected or connection lost.")
                break

            request = data.decode('utf-8', errors='ignore').rstrip("\r\n")
            if not request:
                continue

            parts = request.split(' ', 1)
            cmd = parts[0].upper()
            arg = parts[1].strip() if len(parts) > 1 else ""

            log_server("INFO", client_fd, f"Command received: {cmd}" + (f" {arg}" if arg else ""))

            if cmd == "USER":
                session.username = arg
                send_reply(client_sock, client_fd, 331, "User name okay, need password.")

            elif cmd == "PASS":
                if not session.username:
                    send_reply(client_sock, client_fd, 503, "Bad sequence of commands. Send USER first.")
                elif file_system.verify_user_credentials(session.username, arg):
                    session.is_authenticated = True
                    log_server("INFO", client_fd, f"User '{session.username}' authenticated successfully.")
                    send_reply(client_sock, client_fd, 230, "User logged in, proceed.")
                else:
                    log_server("WARN", client_fd, f"Authentication failed for user '{session.username}'")
                    send_reply(client_sock, client_fd, 530, "Authentication failed.")

            elif not session.is_authenticated and cmd != "QUIT":
                send_reply(client_sock, client_fd, 530, "Please login with USER and PASS.")
                continue

            elif cmd == "PASV":
                port = udp_prepare_passive_listener(session)
                local_ip = get_local_ip(client_sock)
                pasv_ip = local_ip.replace('.', ',')
                pasv_msg = f"Entering Passive Mode ({pasv_ip},{port // 256},{port % 256})"
                send_reply(client_sock, client_fd, 227, pasv_msg)

            elif cmd == "PORT":
                port_args = arg.replace(',', ' ').split()
                if len(port_args) == 6:
                    try:
                        h1, h2, h3, h4, p1, p2 = map(int, port_args)
                        if not all(0 <= h <= 255 for h in (h1, h2, h3, h4)):
                            send_reply(client_sock, client_fd, 501, "Invalid IP or Port range.")
                            continue

                        port = p1 * 256 + p2
                        if port < 1024 or port > 65535:
                            send_reply(client_sock, client_fd, 501, "Port number must be >= 1024.")
                            continue

                        ip = f"{h1}.{h2}.{h3}.{h4}"
                        udp_set_active_target(session, ip, port)
                        send_reply(client_sock, client_fd, 200, "PORT command successful.")
                    except ValueError:
                        send_reply(client_sock, client_fd, 501, "Syntax error in IP/PORT.")
                else:
                    send_reply(client_sock, client_fd, 501, "Syntax error in IP/PORT.")

            elif cmd == "TYPE":
                if arg in ("A", "a"):
                    session.type = TransferType.TYPE_ASCII
                    send_reply(client_sock, client_fd, 200, "Switching to ASCII mode.")
                elif arg in ("I", "i"):
                    session.type = TransferType.TYPE_BINARY
                    send_reply(client_sock, client_fd, 200, "Switching to Binary mode.")
                else:
                    send_reply(client_sock, client_fd, 504, "Command not implemented for that parameter.")

            elif cmd == "PWD":
                vpath = get_ftp_path(session.current_dir, session.root_dir)
                send_reply(client_sock, client_fd, 257, f'"{vpath}" is the current directory.')

            elif cmd == "CWD":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or not file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "Failed to change directory. Invalid path or not a directory.")
                else:
                    session.current_dir = target_path
                    send_reply(client_sock, client_fd, 250, f"Directory successfully changed to {get_ftp_path(target_path, session.root_dir)}")

            elif cmd == "CDUP":
                valid, target_path = resolve_safe_path(session.current_dir, "..", session.root_dir)
                if not valid or not file_system.is_directory(target_path):
                    session.current_dir = session.root_dir
                    send_reply(client_sock, client_fd, 250, "Directory successfully changed to /")
                else:
                    session.current_dir = target_path
                    send_reply(client_sock, client_fd, 250, f"Directory successfully changed to {get_ftp_path(target_path, session.root_dir)}")

            elif cmd == "MKD":
                if not arg:
                    send_reply(client_sock, client_fd, 501, "Syntax error in parameters.")
                    continue
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid:
                    send_reply(client_sock, client_fd, 550, "Create directory operation failed. Path traversal denied.")
                elif file_system.exists(target_path):
                    send_reply(client_sock, client_fd, 550, "Create directory failed. Path already exists.")
                elif file_system.create_directory(target_path):
                    send_reply(client_sock, client_fd, 257, f'"{get_ftp_path(target_path, session.root_dir)}" created.')
                else:
                    send_reply(client_sock, client_fd, 550, "Create directory failed.")

            elif cmd == "RMD":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or not file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "Remove directory failed. Directory does not exist or access denied.")
                elif target_path == session.root_dir:
                    send_reply(client_sock, client_fd, 550, "Cannot remove root directory.")
                elif file_system.remove_directory(target_path):
                    send_reply(client_sock, client_fd, 250, "Directory removed.")
                else:
                    send_reply(client_sock, client_fd, 550, "Remove directory failed. Directory might not be empty.")

            elif cmd in ("LIST", "NLST"):
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or not file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "Directory not found or access denied.")
                    continue

                send_reply(client_sock, client_fd, 150, "File status okay; about to open data connection.")
                listing = (file_system.get_directory_listing(target_path) if cmd == "LIST" 
                           else file_system.get_simple_listing(target_path))

                res = udp_send_buffer(session, listing.encode('utf-8'))
                if res.is_success:
                    send_reply(client_sock, client_fd, 226, "Closing data connection. Transfer successful.")
                else:
                    send_reply(client_sock, client_fd, 426, "Data connection closed; transfer aborted.")

            elif cmd == "STAT":
                if not arg:
                    mode_str = "PASV" if session.mode == DataMode.MODE_PASSIVE else "ACTIVE"
                    status = f"Server status: Connected\r\nMode: {mode_str}\r\nUser: {session.username}"
                    send_reply(client_sock, client_fd, 211, status)
                else:
                    valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                    if not valid:
                        send_reply(client_sock, client_fd, 550, "Directory not found.")
                    else:
                        listing = file_system.get_directory_listing(target_path)
                        send_reply(client_sock, client_fd, 213, f"Status follows:\r\n{listing}End of status.")

            elif cmd == "SIZE":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "Could not get file size.")
                else:
                    size = file_system.get_file_size(target_path)
                    if size >= 0:
                        send_reply(client_sock, client_fd, 213, str(size))
                    else:
                        send_reply(client_sock, client_fd, 550, "Could not get file size.")

            elif cmd == "MDTM":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid:
                    send_reply(client_sock, client_fd, 550, "Could not get file modification time.")
                else:
                    mtime = file_system.get_file_mtime(target_path)
                    if mtime:
                        send_reply(client_sock, client_fd, 213, mtime)
                    else:
                        send_reply(client_sock, client_fd, 550, "Could not get modification time.")

            elif cmd == "HASH":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "File not found or is a directory.")
                else:
                    file_hash = file_system.calculate_sha256(target_path)
                    if file_hash:
                        send_reply(client_sock, client_fd, 200, f"SHA-256 {file_hash}")
                    else:
                        send_reply(client_sock, client_fd, 550, "Failed to calculate hash.")

            elif cmd == "RETR":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid:
                    send_reply(client_sock, client_fd, 550, "Access denied. Path traversal blocked.")
                elif not file_system.exists(target_path) or file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "File not found or is a directory.")
                else:
                    send_reply(client_sock, client_fd, 150, "Opening data connection for file download.")
                    res = udp_send_file(session, target_path)
                    if res.is_success:
                        send_reply(client_sock, client_fd, 226, "Transfer complete.")
                    else:
                        send_reply(client_sock, client_fd, 426, f"Transfer aborted: {res.error_msg}")

            elif cmd in ("STOR", "APPE", "STOU"):
                if cmd == "STOU":
                    unique_name = file_system.generate_unique_filename(session.current_dir, session.control_fd)
                    target_path = os.path.join(session.current_dir, unique_name)
                    send_reply(client_sock, client_fd, 150, f"FILE: {unique_name}")
                else:
                    valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                    if not valid:
                        send_reply(client_sock, client_fd, 550, "Access denied. Invalid target path.")
                        continue
                    send_reply(client_sock, client_fd, 150, "Opening data connection for file upload.")

                is_append = (cmd == "APPE")
                res = udp_receive_file(session, target_path, is_append)
                if res.is_success:
                    send_reply(client_sock, client_fd, 226, "Transfer complete.")
                else:
                    send_reply(client_sock, client_fd, 426, f"Transfer aborted: {res.error_msg}")

            elif cmd == "DELE":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "File not found or is a directory.")
                elif file_system.remove_file(target_path):
                    send_reply(client_sock, client_fd, 250, "File deleted successfully.")
                else:
                    send_reply(client_sock, client_fd, 550, "Delete file failed.")

            elif cmd == "RNFR":
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or not file_system.exists(target_path):
                    send_reply(client_sock, client_fd, 550, "File or directory does not exist.")
                else:
                    session.rename_from_path = target_path
                    send_reply(client_sock, client_fd, 350, "Requested file action pending further information.")

            elif cmd == "RNTO":
                if not session.rename_from_path:
                    send_reply(client_sock, client_fd, 503, "Bad sequence of commands. Send RNFR first.")
                else:
                    valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                    if not valid:
                        send_reply(client_sock, client_fd, 550, "Invalid target path.")
                    elif file_system.rename_path(session.rename_from_path, target_path):
                        send_reply(client_sock, client_fd, 250, "File renamed successfully.")
                    else:
                        send_reply(client_sock, client_fd, 550, "Rename failed.")
                    session.rename_from_path = ""

            elif cmd == "NOOP":
                send_reply(client_sock, client_fd, 200, "NOOP ok.")

            elif cmd == "MODE":
                if arg in ("S", "s"):
                    send_reply(client_sock, client_fd, 200, "Mode set to Stream.")
                else:
                    send_reply(client_sock, client_fd, 504, "Bad MODE parameter.")

            elif cmd == "ABOR":
                udp_abort_transfer(session)
                send_reply(client_sock, client_fd, 226, "Abort command successful.")

            elif cmd == "QUIT":
                log_server("INFO", client_fd, "Client requested disconnection (QUIT).")
                send_reply(client_sock, client_fd, 221, "Goodbye.")
                break

            else:
                log_server("WARN", client_fd, f"Unrecognized or unsupported command: {cmd}")
                send_reply(client_sock, client_fd, 502, "Command not implemented.")

    except Exception as e:
        log_server("ERROR", client_fd, f"Exception in session loop: {e}")
    finally:
        client_sock.close()
        log_server("INFO", client_fd, "Session closed.")

def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server_sock.bind(("0.0.0.0", CONTROL_PORT))
        server_sock.listen(5)
        print(f"[{get_current_timestamp()}] [INFO] [SERVER] TCP Control Engine listening on port {CONTROL_PORT}...")

        while True:
            client_sock, (client_ip, client_port) = server_sock.accept()
            client_thread = threading.Thread(
                target=handle_client_session,
                args=(client_sock, client_ip, client_port),
                daemon=True
            )
            client_thread.start()

    except KeyboardInterrupt:
        print("\n[SERVER] Server shutting down gracefully...")
    finally:
        server_sock.close()

if __name__ == "__main__":
    main()