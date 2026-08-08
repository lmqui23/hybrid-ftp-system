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


class StopAndWaitSender:
    def __init__(
        self,
        sock: socket.socket,
        peer: tuple[str, int],
        context: TransferContext,
        injector: FaultInjector | None = None,
    ):
        self.sock = sock
        self.peer = peer
        self.context = context
        self.config = context.config
        self.injector = injector or FaultInjector()

    def send_file(self, path: str | Path) -> str:
        path = Path(path)

        if not path.is_file():
            raise FileNotFoundError(path)

        file_hash = sha256_file(path)
        try:
            self.context.state = TransferState.RUNNING
            sequence = 0

            with path.open("rb") as file:
                while chunk := file.read(
                    self.config.payload_size
                ):
                    packet = RDTPacket(
                        flags=PacketFlag.DATA,
                        transfer_id=self.context.transfer_id,
                        sequence_number=sequence,
                        payload=chunk,
                    )

                    self._send_and_wait(
                        packet,
                        PacketFlag.ACK,
                        expected_ack=sequence,
                    )

                    self.context.statistics.bytes_transferred += len(
                        chunk
                    )
                    sequence += 1

            fin_packet = RDTPacket(
                flags=PacketFlag.FIN,
                transfer_id=self.context.transfer_id,
                sequence_number=sequence,
            )

            reply = self._send_and_wait(
                fin_packet,
                PacketFlag.FIN_ACK,
                expected_ack=sequence,
            )

            remote_hash = reply.payload.decode("ascii")

            if remote_hash != file_hash:
                raise TransferError("SHA-256 mismatch")

            self.context.finish(TransferState.COMPLETED)
            return file_hash

        except Exception:
            if not self.context.is_cancelled():
                self.context.finish(TransferState.FAILED)
            raise

    def _send_and_wait(
        self,
        packet: RDTPacket,
        expected_flag: PacketFlag,
        expected_ack: int,
    ) -> RDTPacket:
        raw = encode_packet(packet)

        for attempt in range(
            self.config.maximum_retries + 1
        ):
            self._check_cancelled()

            self.injector.sendto(
                self.sock,
                raw,
                self.peer,
            )
            self.context.statistics.packets_sent += 1

            deadline = (
                time.monotonic()
                + self.config.retransmission_timeout
            )

            while time.monotonic() < deadline:
                self._check_cancelled()

                remaining = deadline - time.monotonic()
                self.sock.settimeout(max(remaining, 0.01))

                try:
                    data, address = self.sock.recvfrom(65535)
                    reply = decode_packet(data)
                except socket.timeout:
                    break
                except PacketDecodeError:
                    self.context.statistics.corrupted_packets += 1
                    continue

                if address != self.peer:
                    continue

                if reply.transfer_id != self.context.transfer_id:
                    continue

                if reply.has_flag(PacketFlag.CANCEL):
                    self.context.cancel()
                    raise TransferError("Transfer cancelled by receiver")

                if (
                    reply.has_flag(expected_flag)
                    and reply.acknowledgment_number
                    == expected_ack
                ):
                    self.context.statistics.packets_received += 1
                    if reply.has_flag(PacketFlag.ACK):
                        self.context.statistics.acknowledgments_received += 1
                    return reply

        raise TransferError(
            f"No {expected_flag.name} for "
            f"sequence {expected_ack}"
        )

    def _check_cancelled(self) -> None:
        if not self.context.is_cancelled():
            return

        cancel_packet = RDTPacket(
            flags=PacketFlag.CANCEL,
            transfer_id=self.context.transfer_id,
        )

        try:
            self.sock.sendto(
                encode_packet(cancel_packet),
                self.peer,
            )
        except OSError:
            pass

        raise TransferError("Transfer cancelled")
