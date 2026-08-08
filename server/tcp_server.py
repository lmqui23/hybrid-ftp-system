import os
import sys
import socket
import datetime
import threading
import hashlib
from pathlib import Path
import secrets
import signal

# Thêm đường dẫn gốc của dự án và thư mục hiện tại vào sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import các module theo đúng cấu trúc thư mục.
from common.data_transfer import (
    udp_prepare_passive_listener,
    udp_set_active_target,
    udp_send_buffer,
    udp_send_file,
    udp_receive_file,
    udp_abort_transfer
)
from common.session import DataMode, FTPSession, TransferType
from server import file_system

CONTROL_PORT = int(os.getenv("FTP_CONTROL_PORT", "2121"))
BUFFER_SIZE = 1024
ACTIVE_SESSIONS: dict[int, FTPSession] = {}
SESSIONS_LOCK = threading.Lock()
REPLY_LOCKS: dict[int, threading.Lock] = {}
SHUTDOWN_EVENT = threading.Event()

def get_current_timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_server(level: str, client_fd: int, message: str) -> None:
    print(f"[{get_current_timestamp()}] [{level}] [FD {client_fd}] {message}")

def send_reply(client_sock: socket.socket, client_fd: int, code: int, message: str) -> None:
    response = f"{code} {message}\r\n".encode('utf-8')
    try:
        lock = REPLY_LOCKS.setdefault(client_fd, threading.Lock())
        with lock:
            client_sock.sendall(response)
    except Exception as e:
        log_server("ERROR", client_fd, f"Failed to send response or client disconnected: {e}")


def send_multiline_reply(
    client_sock: socket.socket,
    client_fd: int,
    code: int,
    lines: list[str],
) -> None:
    payload = (
        f"{code}-{lines[0]}\r\n"
        + "".join(f"{line}\r\n" for line in lines[1:])
        + f"{code} End\r\n"
    ).encode("utf-8")
    lock = REPLY_LOCKS.setdefault(client_fd, threading.Lock())
    with lock:
        client_sock.sendall(payload)


def log_session_table() -> None:
    with SESSIONS_LOCK:
        sessions = list(ACTIVE_SESSIONS.values())
    print("[ACTIVE SESSIONS]")
    print("FD   CLIENT                  USER        MODE     TRANSFER")
    for item in sessions:
        mode = item.mode.name.removeprefix("MODE_")
        print(
            f"{item.control_fd:<4} "
            f"{item.client_ip + ':' + str(item.client_port):<23} "
            f"{(item.username or '-'):11} {mode:8} "
            f"{'ACTIVE' if item.transfer_active else 'IDLE'}"
        )
    if not sessions:
        print("(none)")


def parse_upload_metadata(arg: str) -> tuple[str, int, str] | None:
    parts = arg.split()
    if len(parts) < 2:
        return None

    values = {}
    while parts and "=" in parts[-1]:
        key, value = parts.pop().split("=", 1)
        values[key.upper()] = value

    try:
        size = int(values["SIZE"])
        file_hash = values["SHA256"].lower()
    except (KeyError, ValueError):
        return None

    if size < 0 or len(file_hash) != 64:
        return None
    return " ".join(parts), size, file_hash


def start_transfer(
    session: FTPSession,
    client_sock: socket.socket,
    client_fd: int,
    job,
) -> None:
    session.transfer_active = True
    session.abort_requested = False
    log_session_table()

    def worker() -> None:
        try:
            result = job()
            if session.abort_requested:
                return
            if result.is_success:
                send_reply(client_sock, client_fd, 226, "Transfer complete.")
            else:
                send_reply(
                    client_sock,
                    client_fd,
                    426,
                    f"Transfer aborted: {result.error_msg}",
                )
        finally:
            session.transfer_active = False
            session.abort_requested = False
            log_session_table()

    threading.Thread(target=worker, daemon=True).start()

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

def send_multiline_reply(sock, fd, code, lines):
    message = f"{code}-{lines[0]}\r\n"

    for line in lines[1:]:
        message += f"{line}\r\n"

    message += f"{code} End of status.\r\n"

    sock.sendall(message.encode("utf-8"))

    
def handle_client_session(client_sock: socket.socket, client_ip: str, client_port: int) -> None:
    client_fd = client_sock.fileno()
    session = FTPSession(client_fd, client_ip, client_port)
    reader = client_sock.makefile("r", encoding="utf-8", errors="ignore", newline="")
    with SESSIONS_LOCK:
        ACTIVE_SESSIONS[client_fd] = session
    log_session_table()

    # Đặt root_dir trỏ mặc định vào storage/server_files
    storage_server = os.path.abspath(os.path.join(PROJECT_ROOT, "storage", "server_files"))
    if os.path.exists(storage_server):
        session.root_dir = storage_server
        session.current_dir = storage_server

    log_server("INFO", client_fd, f"Session initialized for {client_ip}:{client_port}")
    send_reply(client_sock, client_fd, 220, "Welcome to RDT-FTP Server (Hybrid TCP/UDP)")

    try:
        while True:
            request = reader.readline()
            if not request:
                log_server("INFO", client_fd, "Client disconnected or connection lost.")
                break

            request = request.rstrip("\r\n")
            if not request:
                continue

            parts = request.split(' ', 1)
            cmd = parts[0].upper()
            arg = parts[1].strip() if len(parts) > 1 else ""

            log_server("INFO", client_fd, f"Command received: {cmd}" + (f" {arg}" if arg else ""))

            if (
                session.transfer_active
                and cmd not in ("ABOR", "NOOP", "QUIT")
            ):
                send_reply(client_sock, client_fd, 450, "Transfer in progress.")
                continue

            if cmd == "USER":
                session.username = arg
                send_reply(client_sock, client_fd, 331, "User name okay, need password.")

            elif cmd == "PASS":
                if not session.username:
                    send_reply(client_sock, client_fd, 503, "Bad sequence of commands. Send USER first.")
                elif file_system.verify_user_credentials(session.username, arg):
                    session.is_authenticated = True
                    log_server("INFO", client_fd, f"User '{session.username}' authenticated successfully.")
                    log_session_table()
                    send_reply(client_sock, client_fd, 230, "User logged in, proceed.")
                else:
                    log_server("WARN", client_fd, f"Authentication failed for user '{session.username}'")
                    send_reply(client_sock, client_fd, 530, "Authentication failed.")

            elif not session.is_authenticated and cmd != "QUIT":
                send_reply(client_sock, client_fd, 530, "Please login with USER and PASS.")
                continue

            elif cmd == "PASV":
                port = udp_prepare_passive_listener(session)
                if port == 0:
                    send_reply(client_sock, client_fd, 425, "Cannot open data connection.")
                    continue
                local_ip = get_local_ip(client_sock)
                pasv_ip = local_ip.replace('.', ',')
                pasv_msg = f"Entering Passive Mode ({pasv_ip},{port // 256},{port % 256})"
                send_reply(client_sock, client_fd, 227, pasv_msg)

            elif cmd == "PORT":
                # Strict parsing: require exactly six comma-separated numeric parts
                # in the form h1,h2,h3,h4,p1,p2 (no extra tokens).
                if not arg or arg.count(',') != 5:
                    send_reply(client_sock, client_fd, 501, "Syntax error in IP/PORT.")
                    continue

                parts = [part.strip() for part in arg.split(',')]
                if len(parts) != 6:
                    send_reply(client_sock, client_fd, 501, "Syntax error in IP/PORT.")
                    continue

                try:
                    h1, h2, h3, h4, p1, p2 = map(int, parts)
                except ValueError:
                    send_reply(client_sock, client_fd, 501, "Syntax error in IP/PORT.")
                    continue

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
                if session.mode == DataMode.MODE_NONE:
                    send_reply(client_sock, client_fd, 425, "Use PASV or PORT first.")
                    continue
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid or not file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "Directory not found or access denied.")
                    continue

                listing = (file_system.get_directory_listing(target_path) if cmd == "LIST" 
                           else file_system.get_simple_listing(target_path))
                payload = listing.encode("utf-8")
                transfer_id = secrets.randbits(64)
                file_hash = hashlib.sha256(payload).hexdigest()
                send_reply(
                    client_sock,
                    client_fd,
                    150,
                    f"TID={transfer_id} SIZE={len(payload)} SHA256={file_hash}",
                )
                start_transfer(
                    session,
                    client_sock,
                    client_fd,
                    lambda data=payload, tid=transfer_id: udp_send_buffer(
                        session,
                        data,
                        tid,
                    ),
                )

            elif cmd == "STAT":
                if not session.is_authenticated:
                    send_reply(
                        client_sock,
                        client_fd,
                        530,
                        "Please login with USER and PASS.",
                    )

                elif not arg:
                    mode_str = (
                        "PASV"
                        if session.mode == DataMode.MODE_PASSIVE
                        else "ACTIVE"
                    )

                    # FTP multi-line response
                    response = (
                        f"211-Server status: Connected\r\n"
                        f"Mode: {mode_str}\r\n"
                        f"User: {session.username or '-'}\r\n"
                        f"211 End of status.\r\n"
                    )

                    client_sock.sendall(response.encode("utf-8"))

                else:
                    valid, target_path = resolve_safe_path(
                        session.current_dir,
                        arg,
                        session.root_dir,
                    )

                    if not valid or not file_system.exists(target_path):
                        send_reply(
                            client_sock,
                            client_fd,
                            550,
                            "File or directory not found.",
                        )

                    else:
                        if file_system.is_directory(target_path):
                            listing = file_system.get_directory_listing(target_path)

                            response = (
                                f"213-Status follows:\r\n"
                                f"{listing}"
                                f"213 End of status.\r\n"
                            )

                            client_sock.sendall(response.encode("utf-8"))

                        else:
                            size = file_system.get_file_size(target_path)
                            mtime = file_system.get_file_mtime(target_path)
                            sha = file_system.calculate_sha256(target_path)

                            mtime_line = (
                                f"Modify: {mtime}"
                                if mtime
                                else "Modify: unknown"
                            )

                            sha_line = (
                                f"SHA-256: {sha}"
                                if sha
                                else "SHA-256: unknown"
                            )

                            response = (
                                f"213-Status follows:\r\n"
                                f"Size: {size}\r\n"
                                f"{mtime_line}\r\n"
                                f"{sha_line}\r\n"
                                f"213 End of status.\r\n"
                            )

                            client_sock.sendall(response.encode("utf-8"))

            


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
                if session.mode == DataMode.MODE_NONE:
                    send_reply(client_sock, client_fd, 425, "Use PASV or PORT first.")
                    continue
                valid, target_path = resolve_safe_path(session.current_dir, arg, session.root_dir)
                if not valid:
                    send_reply(client_sock, client_fd, 550, "Access denied. Path traversal blocked.")
                elif not file_system.exists(target_path) or file_system.is_directory(target_path):
                    send_reply(client_sock, client_fd, 550, "File not found or is a directory.")
                else:
                    transfer_id = secrets.randbits(64)
                    
                    # Nếu là TYPE A (ASCII): Tạo file tạm đã convert newline trước khi gửi
                    if session.type == TransferType.TYPE_ASCII:
                        import tempfile

                        data = Path(target_path).read_bytes()
                        normalized = data.replace(b"\r\n", b"\n")
                        network = normalized.replace(b"\n", b"\r\n")
                        
                        tmp = tempfile.NamedTemporaryFile(delete=False)
                        try:
                            tmp.write(network)
                            tmp.flush()
                            send_file_path = tmp.name
                        finally:
                            tmp.close()

                        # Tính Size và Hash TRÊN DỮ LIỆU CHUẨN MẠNG (\r\n) sẽ gửi đi
                        size = len(network)
                        file_hash = hashlib.sha256(network).hexdigest()
                        is_temp = True
                    else:
                        send_file_path = target_path
                        size = file_system.get_file_size(target_path)
                        file_hash = file_system.calculate_sha256(target_path)
                        is_temp = False

                    send_reply(
                        client_sock,
                        client_fd,
                        150,
                        f"TID={transfer_id} SIZE={size} SHA256={file_hash}",
                    )

                    def job(path=send_file_path, tid=transfer_id, cleanup=is_temp):
                        try:
                            return udp_send_file(session, path, tid)
                        finally:
                            if cleanup:
                                try:
                                    Path(path).unlink(missing_ok=True)
                                except Exception:
                                    pass

                    start_transfer(
                        session,
                        client_sock,
                        client_fd,
                        job,
                    )
            

            elif cmd in ("STOR", "APPE", "STOU"):
                if session.mode == DataMode.MODE_NONE:
                    send_reply(client_sock, client_fd, 425, "Use PASV or PORT first.")
                    continue

                metadata = parse_upload_metadata(arg)
                if metadata is None:
                    send_reply(
                        client_sock, client_fd, 501, "Upload requires SIZE and SHA256."
                    )
                    continue

                upload_name, expected_size, expected_hash = metadata
                transfer_id = secrets.randbits(64)

                if cmd == "STOU":
                    unique_name = file_system.generate_unique_filename(
                        session.current_dir,
                        session.control_fd,
                        original_filename=upload_name,
                    )
                    # Đi qua resolve_safe_path để đảm bảo luôn nằm trong session.root_dir
                    valid, target_path = resolve_safe_path(
                        session.current_dir, unique_name, session.root_dir
                    )
                    if not valid:
                        send_reply(
                            client_sock,
                            client_fd,
                            550,
                            "Access denied. Generated path is invalid.",
                        )
                        continue
                    extra = f" FILE={unique_name}"
                else:
                    valid, target_path = resolve_safe_path(
                        session.current_dir, upload_name, session.root_dir
                    )
                    if not valid:
                        send_reply(
                            client_sock,
                            client_fd,
                            550,
                            "Access denied. Invalid target path.",
                        )
                        continue
                    extra = ""

                is_append = cmd == "APPE"

                send_reply(
                    client_sock,
                    client_fd,
                    150,
                    f"TID={transfer_id} SIZE={expected_size} SHA256={expected_hash}{extra}",
                )

                start_transfer(
                    session,
                    client_sock,
                    client_fd,
                    lambda path=target_path, tid=transfer_id, size=expected_size, digest=expected_hash, append=is_append: udp_receive_file(
                        session,
                        path,
                        tid,
                        size,
                        digest,
                        append,
                    ),
                )

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
                if session.transfer_active:
                    udp_abort_transfer(session)
                    send_reply(client_sock, client_fd, 426, "Transfer aborted.")
                else:
                    send_reply(client_sock, client_fd, 225, "No transfer in progress.")

            elif cmd == "HELP":
                details = {
                    "USER": "USER <username> - specify username for login.",
                    "PASS": "PASS <password> - provide password to authenticate.",
                    "PWD": "PWD - print working directory.",
                    "CWD": "CWD <dir> - change working directory.",
                    "CDUP": "CDUP - change to parent directory.",
                    "MKD": "MKD <dir> - create a new directory.",
                    "RMD": "RMD <dir> - remove a directory.",
                    "LIST": "LIST [dir] - list directory with details (uses data connection).",
                    "NLST": "NLST [dir] - list names only (uses data connection).",
                    "STAT": "STAT [path] - server status or file/dir metadata.",
                    "SIZE": "SIZE <file> - return size of the file in bytes.",
                    "MDTM": "MDTM <file> - return modification time (YYYYMMDDhhmmss).",
                    "TYPE": "TYPE A|I - set transfer type (ASCII or Binary).",
                    "MODE": "MODE S - set transfer mode (Stream supported).",
                    "PORT": "PORT h1,h2,h3,h4,p1,p2 - set active data target.",
                    "PASV": "PASV - enter passive data mode and return UDP port.",
                    "RETR": "RETR <file> - download a file through UDP RDT.",
                    "STOR": "STOR <file> SIZE=<n> SHA256=<hash> - upload a file through UDP RDT.",
                    "STOU": "STOU SIZE=<n> SHA256=<hash> - store with unique filename; server returns FILE=<name>.",
                    "APPE": "APPE <file> SIZE=<n> SHA256=<hash> - append to existing file.",
                    "DELE": "DELE <file> - delete a file.",
                    "RNFR": "RNFR <from> - rename from (prepare).",
                    "RNTO": "RNTO <to> - rename to (complete rename).",
                    "HASH": "HASH <file> - compute SHA-256 of a file.",
                    "ABOR": "ABOR - abort active transfer.",
                    "NOOP": "NOOP - no operation (keepalive).",
                    "QUIT": "QUIT - close the connection.",
                    "HELP": "HELP [command] - show help information.",
                }
                if arg:
                    key = arg.strip().upper()
                else:
                    key = ""

                if key and key in details:
                    send_reply(client_sock, client_fd, 214, details[key])
                else:
                    # Compose a friendly multi-line help summary
                    send_multiline_reply(
                        client_sock,
                        client_fd,
                        214,
                        [
                            "Supported commands",
                            "USER PASS QUIT NOOP HELP",
                            "PWD CWD CDUP MKD RMD",
                            "LIST NLST STAT SIZE MDTM HASH",
                            "TYPE MODE PORT PASV",
                            "RETR STOR STOU APPE DELE",
                            "RNFR RNTO ABOR",
                        ],
                    )

            elif cmd == "QUIT":
                if session.transfer_active:
                    udp_abort_transfer(session)
                log_server("INFO", client_fd, "Client requested disconnection (QUIT).")
                send_reply(client_sock, client_fd, 221, "Goodbye.")
                break

            else:
                log_server("WARN", client_fd, f"Unrecognized or unsupported command: {cmd}")
                send_reply(client_sock, client_fd, 502, "Command not implemented.")

    except Exception as e:
        log_server("ERROR", client_fd, f"Exception in session loop: {e}")
    finally:
        reader.close()
        client_sock.close()
        with SESSIONS_LOCK:
            ACTIVE_SESSIONS.pop(client_fd, None)
        REPLY_LOCKS.pop(client_fd, None)
        log_session_table()
        log_server("INFO", client_fd, "Session closed.")

def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def _signal_handler(signum, frame):
        # Mark shutdown requested; main loop will exit gracefully.
        print(f"\n[{get_current_timestamp()}] [INFO] [SERVER] Received signal {signum}, shutting down...")
        SHUTDOWN_EVENT.set()

    # Register SIGINT handler so Ctrl-C sets the shutdown event reliably.
    try:
        signal.signal(signal.SIGINT, _signal_handler)
    except Exception:
        # On some platforms or embeded environments signal registration may fail.
        pass

    try:
        server_sock.bind(("0.0.0.0", CONTROL_PORT))
        server_sock.listen(5)
        server_sock.settimeout(1.0)
        print(f"[{get_current_timestamp()}] [INFO] [SERVER] TCP Control Engine listening on port {CONTROL_PORT}...")

        while not SHUTDOWN_EVENT.is_set():
            try:
                client_sock, (client_ip, client_port) = server_sock.accept()
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                SHUTDOWN_EVENT.set()
                break

            client_thread = threading.Thread(
                target=handle_client_session,
                args=(client_sock, client_ip, client_port),
                daemon=True,
            )
            client_thread.start()

    finally:
        try:
            server_sock.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
