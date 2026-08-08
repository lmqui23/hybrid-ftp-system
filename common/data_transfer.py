import shutil
import socket
import tempfile
import threading
import os
import time
from pathlib import Path
import hashlib
from typing import Callable

from common.session import DataMode, FTPSession, TransferType
from rdt.config import RDTConfig
from rdt.context import TransferContext
from rdt.fault_injector import FaultConfig, FaultInjector
from rdt.receiver import StopAndWaitReceiver
from rdt.sender import StopAndWaitSender


READY = b"RDT-READY"

# Optional hook used by a CLI to fetch the current input buffer so
# progress printing can redraw the prompt without corrupting user input.
_input_getter: Callable[[], str] | None = None
# Lock to synchronize progress printing and prompt redraws.
_print_lock = threading.Lock()


def register_input_getter(func: Callable[[], str]) -> None:
    """Register a callable that returns the current CLI input buffer.

    The progress printer will call this to redraw the prompt while
    preserving user-typed characters.
    """
    global _input_getter
    _input_getter = func


def unregister_input_getter() -> None:
    """Remove previously registered input getter."""
    global _input_getter
    _input_getter = None


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


def _fault_injector() -> FaultInjector:
    return FaultInjector(
        FaultConfig(
            loss_rate=float(os.getenv("RDT_LOSS_RATE", "0")),
            corruption_rate=float(os.getenv("RDT_CORRUPTION_RATE", "0")),
            duplicate_rate=float(os.getenv("RDT_DUPLICATE_RATE", "0")),
        )
    )


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
        if session.abort_requested:
            context.cancel()

        StopAndWaitSender(
            sock,
            peer,
            context,
            _fault_injector(),
        ).send_file(file_path)

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

        if session.abort_requested:
            context.cancel()

        is_ascii = session.type == TransferType.TYPE_ASCII

        StopAndWaitReceiver(
            sock,
            context,
            _fault_injector(),
        ).receive_file(
            upload_path,
            expected_size,
            expected_hash,
        )

        destination = Path(file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if is_ascii:
            # Read raw uploaded bytes (network form, typically using CRLF).
            data = upload_path.read_bytes()

            # Verify hash against raw network bytes first (expected_hash
            # is assumed to be computed on the network form).
            if expected_hash:
                actual_hash = hashlib.sha256(data).hexdigest()
                if actual_hash.lower() != expected_hash.lower():
                    raise ValueError(
                        f"ASCII Hash mismatch: "
                        f"expected {expected_hash}, "
                        f"got {actual_hash}"
                    )

            # Only after verification, convert CRLF -> LF for storage.
            converted = data.replace(b"\r\n", b"\n")

            if is_append:
                with destination.open("ab") as output:
                    output.write(converted)
            else:
                tmp_dest = destination.with_suffix(".tmp.ascii")
                tmp_dest.write_bytes(converted)
                tmp_dest.replace(destination)

            upload_path.unlink(missing_ok=True)

        else:
            if is_append:
                with destination.open("ab") as output, \
                     upload_path.open("rb") as source:
                 shutil.copyfileobj(source, output)

                upload_path.unlink()
            else:
                upload_path.replace(destination)

        return TransferResult(
            True,
            context.statistics.bytes_transferred,
        )

    except Exception as error:
        upload_path.unlink(missing_ok=True)

        return TransferResult(
            False,
            error_msg=str(error),
        )

    finally:
        session.transfer_context = None

        if sock is not None:
            sock.close()

        session.pasv_udp_sock = None
        session.mode = DataMode.MODE_NONE


def udp_abort_transfer(session: FTPSession) -> bool:
    session.abort_requested = True
    if session.transfer_context is not None:
        session.transfer_context.cancel()
        return True
    close_data_socket(session)
    return session.transfer_active


_client_socket: socket.socket | None = None
_client_peer: tuple[str, int] | None = None
_client_mode = DataMode.MODE_NONE
_client_context: TransferContext | None = None
_client_abort_requested = False


def _close_client_socket() -> None:
    global _client_socket, _client_peer, _client_mode
    global _client_context, _client_abort_requested
    if _client_socket is not None:
        try:
            _client_socket.close()
        except OSError:
            pass
    _client_socket = None
    _client_peer = None
    _client_mode = DataMode.MODE_NONE
    _client_context = None
    _client_abort_requested = False


def udp_client_abort_transfer() -> None:
    global _client_abort_requested
    _client_abort_requested = True
    if _client_context is not None:
        _client_context.cancel()


def _progress(context: TransferContext, total: int, done: threading.Event) -> None:
    # Progress printer that cooperates with a CLI input loop.
    # It uses a print lock and optional input getter (set by client)
    # to redraw the prompt + current buffer after printing progress.
    global _input_getter, _print_lock
    while not done.wait(0.25):
        transferred = context.statistics.bytes_transferred
        percent = 100 if total == 0 else min(100, transferred * 100 // total)
        line = (
            f"[RDT] {percent:3d}% | {transferred}/{total} bytes | "
            f"retries={context.statistics.retransmissions}"
        )
        with _print_lock:
            # Print progress on its own line, then redraw prompt and buffer.
            print(f"\r{line}")
            if _input_getter is not None:
                buf = _input_getter()
                # Reprint prompt and current buffer without newline.
                print(f"ftp> {buf}", end="", flush=True)


def _finish_progress(context: TransferContext, total: int) -> None:
    stats = context.statistics
    line = (
        f"[RDT] 100% | {stats.bytes_transferred}/{total} bytes | "
        f"retries={stats.retransmissions} | {stats.duration_seconds:.3f}s"
    )
    with _print_lock:
        print(line)
        if _input_getter is not None:
            buf = _input_getter()
            print(f"ftp> {buf}", end="", flush=True)


def udp_client_set_passive(ip: str, port: int) -> None:
    global _client_socket, _client_peer, _client_mode, _client_abort_requested
    _close_client_socket()
    _client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _client_socket.bind(("0.0.0.0", 0))
    _client_peer = (ip, port)
    _client_mode = DataMode.MODE_PASSIVE
    _client_abort_requested = False


def udp_client_prepare_active(local_ip: str) -> tuple[str, int]:
    global _client_socket, _client_mode, _client_abort_requested
    _close_client_socket()
    _client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _client_socket.bind((local_ip, 0))
    _client_mode = DataMode.MODE_ACTIVE
    _client_abort_requested = False
    return _client_socket.getsockname()


def udp_client_set_active(local_ip: str, port: int) -> None:
   
    global _client_socket, _client_mode, _client_abort_requested
    _close_client_socket()
    _client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    _client_socket.bind((local_ip, port))
    _client_mode = DataMode.MODE_ACTIVE
    _client_abort_requested = False


def _client_receiver(transfer_id: int) -> StopAndWaitReceiver:
    global _client_context
    if _client_socket is None:
        raise OSError("Use PASV or PORT before a data command")
    if _client_mode == DataMode.MODE_PASSIVE:
        if _client_peer is None:
            raise OSError("Passive server address is missing")
        _client_socket.sendto(READY, _client_peer)
    _client_context = TransferContext(transfer_id, RDTConfig())
    if _client_abort_requested:
        _client_context.cancel()
    return StopAndWaitReceiver(
        _client_socket,
        _client_context,
        _fault_injector(),
    )


def udp_client_receive_file(
    save_path: str,
    transfer_id: int,
    expected_size: int,
    expected_hash: str,
) -> bool:
    done = threading.Event()
    try:
        receiver = _client_receiver(transfer_id)
        monitor = threading.Thread(
            target=_progress,
            args=(_client_context, expected_size, done),
            daemon=True,
        )
        monitor.start()
        receiver.receive_file(save_path, expected_size, expected_hash)
        done.set()
        monitor.join()
        _finish_progress(_client_context, expected_size)
        return True
    except Exception as error:
        print(f"[UDP Client Error] {error}")
        return False
    finally:
        done.set()
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
    global _client_peer, _client_context
    done = threading.Event()
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
        _client_context = context
        if _client_abort_requested:
            context.cancel()
        total = Path(file_path).stat().st_size
        monitor = threading.Thread(
            target=_progress,
            args=(context, total, done),
            daemon=True,
        )
        monitor.start()
        StopAndWaitSender(
            _client_socket,
            _client_peer,
            context,
            _fault_injector(),
        ).send_file(file_path)
        done.set()
        monitor.join()
        _finish_progress(context, total)
        return True
    except Exception as error:
        print(f"[UDP Client Error] {error}")
        return False
    finally:
        done.set()
        _close_client_socket()
