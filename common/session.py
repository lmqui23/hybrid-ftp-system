import socket
import threading
from enum import Enum

from rdt.context import TransferContext


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
        self.root_dir = "/tmp"
        self.current_dir = "/tmp"
        self.rename_from_path = ""
        self.data_ip = ""
        self.data_port = 0
        self.pasv_udp_sock: socket.socket | None = None
        self.transfer_context: TransferContext | None = None
        self.transfer_active = False
        self.abort_requested = False
        self.transfer_lock = threading.Lock()
        self.reply_lock = threading.Lock()
