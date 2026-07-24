import socket
import time
from pathlib import Path

from protocol.file_hash import sha256_file
from protocol.codec import PacketDecodeError, decode_packet, encode_packet
from protocol.packet import PacketFlag, RDTPacket
from rdt.context import TransferContext, TransferState
from rdt.fault_injector import FaultInjector


class TransferError(IOError):
    pass


class StopAndWaitReceiver:
    def __init__(
        self,
        sock: socket.socket,
        context: TransferContext,
        injector: FaultInjector | None = None,
    ):
        self.sock = sock
        self.context = context
        self.config = context.config
        self.peer: tuple[str, int] | None = None
        self.injector = injector or FaultInjector()

    def receive_file(
        self,
        destination: str | Path,
        expected_size: int,
        expected_hash: str,
    ) -> str:
        destination = Path(destination)
        temp = destination.with_name(
            f"{destination.name}.part.{self.context.transfer_id}"
        )

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            expected_seq = 0
            received_bytes = 0

            self.context.state = TransferState.RUNNING

            with temp.open("wb") as file:
                while True:
                    packet, address = self._receive_packet()

                    if packet.transfer_id != self.context.transfer_id:
                        continue

                    if self.peer is None:
                        self.peer = address

                    if address != self.peer:
                        continue

                    if packet.has_flag(PacketFlag.CANCEL):
                        self.context.cancel()
                        raise TransferError("Transfer cancelled")

                    if packet.has_flag(PacketFlag.DATA):
                        if packet.sequence_number == expected_seq:
                            file.write(packet.payload)
                            received_bytes += len(packet.payload)

                            if received_bytes > expected_size:
                                raise TransferError(
                                    "Received more data than expected"
                                )

                            self._send_ack(expected_seq)
                            expected_seq += 1

                            self.context.statistics.bytes_transferred = (
                                received_bytes
                            )

                        elif packet.sequence_number < expected_seq:
                            # Packet trùng do ACK trước đó bị mất.
                            self.context.statistics.duplicate_packets += 1
                            self._send_ack(packet.sequence_number)

                        elif expected_seq > 0:
                            # Packet sai thứ tự.
                            self._send_ack(expected_seq - 1)

                        continue

                    if packet.has_flag(PacketFlag.FIN):
                        if packet.sequence_number != expected_seq:
                            continue

                        if received_bytes != expected_size:
                            raise TransferError(
                                "Received file size does not match TCP information"
                            )

                        fin_sequence = packet.sequence_number
                        break

            self.context.state = TransferState.VERIFYING
            received_hash = sha256_file(temp)

            if received_hash != expected_hash:
                raise TransferError("SHA-256 mismatch")

            # Chỉ thay file chính khi toàn bộ dữ liệu hợp lệ.
            temp.replace(destination)

            fin_ack = RDTPacket(
                flags=PacketFlag.FIN_ACK,
                transfer_id=self.context.transfer_id,
                acknowledgment_number=fin_sequence,
                payload=received_hash.encode("ascii"),
            )

            self._send(fin_ack)
            self._linger_for_duplicate_fin(fin_ack, fin_sequence)

            self.context.finish(TransferState.COMPLETED)
            return received_hash

        except Exception:
            temp.unlink(missing_ok=True)

            if not self.context.is_cancelled():
                self.context.finish(TransferState.FAILED)

            raise

    def _receive_packet(
        self,
    ) -> tuple[RDTPacket, tuple[str, int]]:
        deadline = time.monotonic() + self.config.idle_timeout

        while time.monotonic() < deadline:
            if self.context.is_cancelled():
                self._send_cancel()
                raise TransferError("Transfer cancelled")

            self.sock.settimeout(
                self.config.receive_poll_interval
            )

            try:
                data, address = self.sock.recvfrom(65535)
                packet = decode_packet(data)
                self.context.statistics.packets_received += 1
                return packet, address

            except socket.timeout:
                continue

            except PacketDecodeError:
                self.context.statistics.corrupted_packets += 1

        raise TransferError("Receive timeout")

    def _send_cancel(self) -> None:
        if self.peer is None:
            return

        packet = RDTPacket(
            flags=PacketFlag.CANCEL,
            transfer_id=self.context.transfer_id,
        )

        try:
            self.sock.sendto(encode_packet(packet), self.peer)
        except OSError:
            pass

    def _send_ack(self, sequence: int) -> None:
        ack = RDTPacket(
            flags=PacketFlag.ACK,
            transfer_id=self.context.transfer_id,
            acknowledgment_number=sequence,
        )

        self._send(ack)
        self.context.statistics.acknowledgments_sent += 1

    def _send(self, packet: RDTPacket) -> None:
        if self.peer is None:
            raise TransferError("Sender address is unknown")

        self.injector.sendto(
            self.sock,
            encode_packet(packet),
            self.peer,
        )
        self.context.statistics.packets_sent += 1

    def _linger_for_duplicate_fin(
        self,
        fin_ack: RDTPacket,
        fin_sequence: int,
    ) -> None:
        """Gửi lại FIN_ACK nếu FIN_ACK đầu tiên bị mất."""

        deadline = (
            time.monotonic()
            + self.config.retransmission_timeout * 2
        )

        while time.monotonic() < deadline:
            self.sock.settimeout(
                min(
                    self.config.receive_poll_interval,
                    max(deadline - time.monotonic(), 0.01),
                )
            )

            try:
                data, address = self.sock.recvfrom(65535)
                packet = decode_packet(data)
            except (socket.timeout, PacketDecodeError):
                continue

            if (
                address == self.peer
                and packet.transfer_id
                == self.context.transfer_id
                and packet.has_flag(PacketFlag.FIN)
                and packet.sequence_number == fin_sequence
            ):
                self._send(fin_ack)
