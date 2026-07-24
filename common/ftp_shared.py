import os
import socket
from enum import Enum

# ============================================================================
# ENUMS & DATA STRUCTURES
# ============================================================================

class DataMode(Enum):
    MODE_NONE = 0
    MODE_PASSIVE = 1
    MODE_ACTIVE = 2

class TransferType(Enum):
    TYPE_ASCII = 1
    TYPE_BINARY = 2

class FTPSession:
    def __init__(self, control_fd: int, client_ip: str, client_port: int):
        self.control_fd = control_fd
        self.client_ip = client_ip
        self.client_port = client_port
        
        self.is_authenticated = False
        self.username = ""
        
        self.mode = DataMode.MODE_NONE
        self.type = TransferType.TYPE_BINARY
        
        # Virtual Root directory & current working directory
        self.root_dir = "/tmp"
        self.current_dir = "/tmp"
        
        # State for RNFR / RNTO
        self.rename_from_path = ""
        
        # UDP Active/Passive Connection Details
        self.data_ip = ""
        self.data_port = 0
        self.pasv_udp_sock = None

class TransferResult:
    def __init__(self, is_success: bool = True, bytes_transferred: int = 0, error_msg: str = ""):
        self.is_success = is_success
        self.bytes_transferred = bytes_transferred
        self.error_msg = error_msg


# ============================================================================
# SERVER-SIDE UDP DATA TRANSFER FUNCTIONS
# ============================================================================

def udp_prepare_passive_listener(session: FTPSession) -> int:
    """Mở socket UDP để lắng nghe ở chế độ PASV (trả về port được cấp)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))  # Bắt OS cấp port ngẫu nhiên
        session.pasv_udp_sock = sock
        session.mode = DataMode.MODE_PASSIVE
        return sock.getsockname()[1]
    except Exception as e:
        print(f"[UDP Server Error] Failed to prepare passive listener: {e}")
        return 0

def udp_set_active_target(session: FTPSession, ip: str, port: int) -> None:
    """Thiết lập IP/Port đích cho chế độ ACTIVE (PORT command)."""
    session.data_ip = ip
    session.data_port = port
    session.mode = DataMode.MODE_ACTIVE

def udp_send_buffer(session: FTPSession, buffer: bytes) -> TransferResult:
    """Gửi buffer (ví dụ: dữ liệu lệnh LIST) qua UDP."""
    # TODO: Thay thế bằng protocol RDT (Stop-and-Wait / Go-Back-N / Selective Repeat) của bạn ở đây
    print(f"[UDP Server] Sending {len(buffer)} bytes buffer to client...")
    return TransferResult(is_success=True, bytes_transferred=len(buffer))

def udp_send_file(session: FTPSession, file_path: str) -> TransferResult:
    """Gửi file từ Server xuống Client (Lệnh RETR)."""
    # TODO: Thêm logic RDT gửi file UDP
    print(f"[UDP Server] Sending file {file_path} via UDP...")
    return TransferResult(is_success=True)

def udp_receive_file(session: FTPSession, file_path: str, is_append: bool = False) -> TransferResult:
    """Nhận file từ Client lên Server (Lệnh STOR, APPE, STOU)."""
    # TODO: Thêm logic RDT nhận file UDP
    print(f"[UDP Server] Receiving file to {file_path} (append={is_append}) via UDP...")
    return TransferResult(is_success=True)

def udp_abort_transfer(session: FTPSession) -> None:
    """Hủy bỏ tiến trình truyền dữ liệu UDP (Lệnh ABOR)."""
    print("[UDP Server] Transfer aborted.")


# ============================================================================
# CLIENT-SIDE UDP DATA TRANSFER FUNCTIONS (CÁC HÀM CLIENT ĐANG TÌM)
# ============================================================================

def udp_client_receive_buffer() -> bytes:
    """Client nhận buffer dữ liệu (kết quả của LIST / NLST)."""
    # TODO: Ghép logic RDT Receiver của Client vào đây
    print("[UDP Client] Receiving buffer listing via UDP...")
    return b""

def udp_client_receive_file(save_path: str) -> bool:
    """Client nhận file tải về từ Server (Lệnh RETR)."""
    # TODO: Ghép logic RDT Receiver của Client vào đây
    print(f"[UDP Client] Receiving file and saving to {save_path}...")
    return True

def udp_client_send_file(file_path: str) -> bool:
    """Client gửi file lên Server (Lệnh STOR / APPE)."""
    # TODO: Ghép logic RDT Sender của Client vào đây
    print(f"[UDP Client] Sending file {file_path} via UDP...")
    return True