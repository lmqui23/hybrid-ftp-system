import shutil
import socket
import tempfile
import threading
from enum import Enum
from pathlib import Path

from protocol.file_hash import sha256_file
from rdt.config import RDTConfig
from rdt.context import TransferContext
from rdt.receiver import StopAndWaitReceiver
from rdt.sender import StopAndWaitSender


READY = b"RDT-READY"


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
        self.transfer_lock = threading.Lock()


class TransferResult:
    def __init__(
        self,
        is_success: bool = True,
        bytes_transferred: int = 0,
        error_msg: str = "",
    ):
        self.is_success = is_success
        self.bytes_transferred = bytes_transferred
        self.error_msg = error_msg


def udp_prepare_passive_listener(session: FTPSession) -> int:
    close_data_socket(session)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        session.pasv_udp_sock = sock
        session.mode = DataMode.MODE_PASSIVE
        return sock.getsockname()[1]
    except OSError:
        return 0


def udp_set_active_target(session: FTPSession, ip: str, port: int) -> None:
    close_data_socket(session)
    session.data_ip = ip
    session.data_port = port
    session.mode = DataMode.MODE_ACTIVE


def close_data_socket(session: FTPSession) -> None:
    if session.pasv_udp_sock is not None:
        try:
            session.pasv_udp_sock.close()
        except OSError:
            pass
    session.pasv_udp_sock = None


def _server_socket_and_peer(
    session: FTPSession,
    sending: bool,
) -> tuple[socket.socket, tuple[str, int] | None]:
    if session.mode == DataMode.MODE_PASSIVE:
        if session.pasv_udp_sock is None:
            raise OSError("PASV socket is not available")
        sock = session.pasv_udp_sock
        if not sending:
            return sock, None
        sock.settimeout(5)
        data, peer = sock.recvfrom(1024)
        if data != READY:
            raise OSError("Client readiness datagram was not received")
        return sock, peer

    if session.mode == DataMode.MODE_ACTIVE:
        if not session.data_ip or not session.data_port:
            raise OSError("PORT target is not configured")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        peer = (session.data_ip, session.data_port)
        if not sending:
            sock.sendto(READY, peer)
            return sock, None
        return sock, peer

    raise OSError("Use PASV or PORT before a data command")


def udp_send_file(
    session: FTPSession,
    file_path: str,
    transfer_id: int,
) -> TransferResult:
    sock = None
    try:
        sock, peer = _server_socket_and_peer(session, sending=True)
        context = TransferContext(transfer_id, RDTConfig())
        session.transfer_context = context
        StopAndWaitSender(sock, peer, context).send_file(file_path)
        return TransferResult(True, context.statistics.bytes_transferred)
    except Exception as error:
        return TransferResult(False, error_msg=str(error))
    finally:
        session.transfer_context = None
        if sock is not None:
            sock.close()
        session.pasv_udp_sock = None
        session.mode = DataMode.MODE_NONE


def udp_send_buffer(
    session: FTPSession,
    buffer: bytes,
    transfer_id: int,
) -> TransferResult:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "listing.bin"
        path.write_bytes(buffer)
        return udp_send_file(session, str(path), transfer_id)


def udp_receive_file(
    session: FTPSession,
    file_path: str,
    transfer_id: int,
    expected_size: int,
    expected_hash: str,
    is_append: bool = False,
) -> TransferResult:
    sock = None
    upload_path = Path(f"{file_path}.upload.{transfer_id}")
    try:
        sock, _ = _server_socket_and_peer(session, sending=False)
        context = TransferContext(transfer_id, RDTConfig())
        session.transfer_context = context
        StopAndWaitReceiver(sock, context).receive_file(
            upload_path,
            expected_size,
            expected_hash,
        )

        destination = Path(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if is_append:
            with destination.open("ab") as output, upload_path.open("rb") as source:
                shutil.copyfileobj(source, output)
            upload_path.unlink()
        else:
            upload_path.replace(destination)

        return TransferResult(True, context.statistics.bytes_transferred)
    except Exception as error:
        upload_path.unlink(missing_ok=True)
        return TransferResult(False, error_msg=str(error))
    finally:
        session.transfer_context = None
        if sock is not None:
            sock.close()
        session.pasv_udp_sock = None
        session.mode = DataMode.MODE_NONE


def udp_abort_transfer(session: FTPSession) -> None:
    if session.transfer_context is not None:
        session.transfer_context.cancel()


_client_socket: socket.socket | None = None
_client_peer: tuple[str, int] | None = None
_client_mode = DataMode.MODE_NONE


def _close_client_socket() -> None:
    global _client_socket, _client_peer, _client_mode
    if _client_socket is not None:
        try:
            _client_socket.close()
        except OSError:
            pass
    _client_socket = None
    _client_peer = None
    _client_mode = DataMode.MODE_NONE


def udp_client_set_passive(ip: str, port: int) -> None:
    global _client_socket, _client_peer, _client_mode
    _close_client_socket()
    _client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _client_socket.bind(("0.0.0.0", 0))
    _client_peer = (ip, port)
    _client_mode = DataMode.MODE_PASSIVE


def udp_client_prepare_active(local_ip: str) -> tuple[str, int]:
    global _client_socket, _client_mode
    _close_client_socket()
    _client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _client_socket.bind((local_ip, 0))
    _client_mode = DataMode.MODE_ACTIVE
    return _client_socket.getsockname()


def _client_receiver(transfer_id: int) -> StopAndWaitReceiver:
    if _client_socket is None:
        raise OSError("Use PASV or PORT before a data command")
    if _client_mode == DataMode.MODE_PASSIVE:
        if _client_peer is None:
            raise OSError("Passive server address is missing")
        _client_socket.sendto(READY, _client_peer)
    return StopAndWaitReceiver(
        _client_socket,
        TransferContext(transfer_id, RDTConfig()),
    )


def udp_client_receive_file(
    save_path: str,
    transfer_id: int,
    expected_size: int,
    expected_hash: str,
) -> bool:
    try:
        receiver = _client_receiver(transfer_id)
        receiver.receive_file(save_path, expected_size, expected_hash)
        return True
    except Exception as error:
        print(f"[UDP Client Error] {error}")
        return False
    finally:
        _close_client_socket()


def udp_client_receive_buffer(
    transfer_id: int,
    expected_size: int,
    expected_hash: str,
) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "listing.bin"
        if not udp_client_receive_file(
            str(path),
            transfer_id,
            expected_size,
            expected_hash,
        ):
            return b""
        return path.read_bytes()


def udp_client_send_file(file_path: str, transfer_id: int) -> bool:
    global _client_peer
    try:
        if _client_socket is None:
            raise OSError("Use PASV or PORT before a data command")
        if _client_mode == DataMode.MODE_ACTIVE:
            _client_socket.settimeout(5)
            data, _client_peer = _client_socket.recvfrom(1024)
            if data != READY:
                raise OSError("Server readiness datagram was not received")
        if _client_peer is None:
            raise OSError("UDP server address is missing")
        context = TransferContext(transfer_id, RDTConfig())
        StopAndWaitSender(
            _client_socket,
            _client_peer,
            context,
        ).send_file(file_path)
        return True
    except Exception as error:
        print(f"[UDP Client Error] {error}")
        return False
    finally:
        _close_client_socket()
