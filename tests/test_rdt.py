import hashlib
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from protocol.codec import decode_packet, encode_packet
from protocol.packet import PacketFlag, RDTPacket
from rdt.config import RDTConfig
from rdt.context import TransferContext, TransferState
from rdt.fault_injector import FaultConfig, FaultInjector
from rdt.receiver import StopAndWaitReceiver, TransferError
from rdt.sender import StopAndWaitSender, TransferError as SenderTransferError


class RDTTests(unittest.TestCase):
    def transfer(
        self,
        content: bytes,
        with_faults: bool = False,
        send_wrong_transfer: bool = False,
        sender_injector=None,
        receiver_injector=None,
    ):
        config = RDTConfig(
            payload_size=256,
            retransmission_timeout=0.05,
            idle_timeout=3,
            maximum_retries=100,
            receive_poll_interval=0.02,
        )
        sender_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender_socket.bind(("127.0.0.1", 0))
        receiver_socket.bind(("127.0.0.1", 0))

        sender_context = TransferContext(77, config)
        receiver_context = TransferContext(77, config)

        if with_faults:
            sender_injector = FaultInjector(
                FaultConfig(0.1, 0.1, 0.1, seed=10)
            )
            receiver_injector = FaultInjector(
                FaultConfig(0.1, 0.1, 0.1, seed=20)
            )

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            destination = Path(directory) / "destination.bin"
            source.write_bytes(content)
            expected_hash = hashlib.sha256(content).hexdigest()
            errors = []

            receiver = StopAndWaitReceiver(
                receiver_socket,
                receiver_context,
                receiver_injector,
            )

            def receive():
                try:
                    receiver.receive_file(
                        destination,
                        len(content),
                        expected_hash,
                    )
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=receive)
            worker.start()

            try:
                if send_wrong_transfer:
                    stray_socket = socket.socket(
                        socket.AF_INET,
                        socket.SOCK_DGRAM,
                    )
                    stray_socket.sendto(
                        encode_packet(
                            RDTPacket(
                                PacketFlag.DATA,
                                transfer_id=999,
                                payload=b"wrong transfer",
                            )
                        ),
                        receiver_socket.getsockname(),
                    )
                    stray_socket.close()

                sender = StopAndWaitSender(
                    sender_socket,
                    receiver_socket.getsockname(),
                    sender_context,
                    sender_injector,
                )
                sender.send_file(source)
                worker.join(5)

                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(destination.read_bytes(), content)
                self.assertEqual(sender_context.state, TransferState.COMPLETED)
                self.assertEqual(
                    receiver_context.state,
                    TransferState.COMPLETED,
                )
            finally:
                sender_socket.close()
                receiver_socket.close()

        return sender_context, receiver_context

    def test_empty_file(self):
        self.transfer(b"")

    def test_ascii_text_file(self):
        self.transfer("Hybrid FTP\nTiếng Việt\n".encode("utf-8"))

    def test_binary_file(self):
        self.transfer(bytes(range(256)) * 20)

    def test_loss_corruption_and_duplicates(self):
        sender, receiver = self.transfer(
            bytes(range(256)) * 40,
            with_faults=True,
        )
        self.assertGreater(
            sender.statistics.corrupted_packets
            + receiver.statistics.corrupted_packets,
            0,
        )
        self.assertGreater(receiver.statistics.duplicate_packets, 0)

    def test_wrong_transfer_id_is_ignored(self):
        self.transfer(b"correct data", send_wrong_transfer=True)

    def test_lost_fin_ack_is_recovered(self):
        class DropFirstFinAck(FaultInjector):
            def __init__(self):
                super().__init__()
                self.dropped = False

            def sendto(self, sock, data, address):
                packet = decode_packet(data)
                if packet.has_flag(PacketFlag.FIN_ACK) and not self.dropped:
                    self.dropped = True
                    return len(data)
                return super().sendto(sock, data, address)

        injector = DropFirstFinAck()
        sender, receiver = self.transfer(
            b"FIN retry",
            receiver_injector=injector,
        )
        self.assertTrue(injector.dropped)
        self.assertEqual(receiver.state, TransferState.COMPLETED)

    def test_out_of_order_packet_is_not_written_early(self):
        config = RDTConfig(
            payload_size=64,
            retransmission_timeout=0.05,
            idle_timeout=1,
            maximum_retries=3,
            receive_poll_interval=0.01,
        )
        receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver_socket.bind(("127.0.0.1", 0))
        sender_socket.bind(("127.0.0.1", 0))
        context = TransferContext(93, config)
        content = b"first-second"
        expected_hash = hashlib.sha256(content).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "ordered.bin"
            errors = []

            def receive():
                try:
                    StopAndWaitReceiver(receiver_socket, context).receive_file(
                        destination,
                        len(content),
                        expected_hash,
                    )
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=receive)
            worker.start()
            peer = receiver_socket.getsockname()

            def send(packet):
                sender_socket.sendto(encode_packet(packet), peer)

            send(RDTPacket(PacketFlag.DATA, 93, sequence_number=1, payload=b"second"))
            send(RDTPacket(PacketFlag.DATA, 93, sequence_number=0, payload=b"first-"))
            sender_socket.settimeout(1)
            ack0 = decode_packet(sender_socket.recvfrom(65535)[0])
            self.assertEqual(ack0.acknowledgment_number, 0)
            send(RDTPacket(PacketFlag.DATA, 93, sequence_number=1, payload=b"second"))
            ack1 = decode_packet(sender_socket.recvfrom(65535)[0])
            self.assertEqual(ack1.acknowledgment_number, 1)
            send(RDTPacket(PacketFlag.FIN, 93, sequence_number=2))
            fin_ack = decode_packet(sender_socket.recvfrom(65535)[0])
            self.assertTrue(fin_ack.has_flag(PacketFlag.FIN_ACK))
            worker.join(2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(destination.read_bytes(), content)

        sender_socket.close()
        receiver_socket.close()

    def test_receiver_timeout_removes_temporary_file(self):
        config = RDTConfig(
            idle_timeout=0.05,
            receive_poll_interval=0.01,
        )
        context = TransferContext(94, config)
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.bind(("127.0.0.1", 0))

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "timeout.bin"
            with self.assertRaises(TransferError):
                StopAndWaitReceiver(udp_socket, context).receive_file(
                    destination,
                    1,
                    "0" * 64,
                )
            self.assertEqual(context.state, TransferState.FAILED)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob("*.part.*")), [])

        udp_socket.close()

    def test_cancelled_receiver_removes_temporary_file(self):
        config = RDTConfig(idle_timeout=0.1)
        context = TransferContext(88, config)
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.bind(("127.0.0.1", 0))

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cancelled.bin"
            context.cancel()

            with self.assertRaises(TransferError):
                StopAndWaitReceiver(udp_socket, context).receive_file(
                    destination,
                    1,
                    "unused",
                )

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob("*.part.*")), [])

        udp_socket.close()

    def test_invalid_config_is_rejected(self):
        with self.assertRaises(ValueError):
            RDTConfig(payload_size=0)
        with self.assertRaises(ValueError):
            RDTConfig(retransmission_timeout=0)

    def test_total_packet_loss_reaches_retry_limit(self):
        config = RDTConfig(
            retransmission_timeout=0.01,
            idle_timeout=0.1,
            maximum_retries=2,
            receive_poll_interval=0.01,
        )
        context = TransferContext(91, config)
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.bind(("127.0.0.1", 0))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            source.write_bytes(b"network failure")
            sender = StopAndWaitSender(
                udp_socket,
                ("127.0.0.1", 9),
                context,
                FaultInjector(FaultConfig(loss_rate=1.0, seed=1)),
            )
            with self.assertRaises(SenderTransferError):
                sender.send_file(source)

        udp_socket.close()
        self.assertEqual(context.state, TransferState.FAILED)

    def test_hash_failure_preserves_existing_destination(self):
        config = RDTConfig(
            payload_size=64,
            retransmission_timeout=0.02,
            idle_timeout=0.2,
            maximum_retries=2,
            receive_poll_interval=0.01,
        )
        sender_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender_socket.bind(("127.0.0.1", 0))
        receiver_socket.bind(("127.0.0.1", 0))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.bin"
            destination = Path(directory) / "destination.bin"
            source.write_bytes(b"new content")
            destination.write_bytes(b"original content")
            receiver_context = TransferContext(92, config)
            errors = []

            def receive():
                try:
                    StopAndWaitReceiver(
                        receiver_socket,
                        receiver_context,
                    ).receive_file(
                        destination,
                        source.stat().st_size,
                        "0" * 64,
                    )
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=receive)
            worker.start()
            sender_context = TransferContext(92, config)
            with self.assertRaises(SenderTransferError):
                StopAndWaitSender(
                    sender_socket,
                    receiver_socket.getsockname(),
                    sender_context,
                ).send_file(source)
            worker.join(2)

            self.assertTrue(errors)
            self.assertEqual(destination.read_bytes(), b"original content")
            self.assertEqual(list(Path(directory).glob("*.part.*")), [])

        sender_socket.close()
        receiver_socket.close()


if __name__ == "__main__":
    unittest.main()
