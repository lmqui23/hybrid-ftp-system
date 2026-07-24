import hashlib
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from protocol.codec import encode_packet
from protocol.packet import PacketFlag, RDTPacket
from rdt.config import RDTConfig
from rdt.context import TransferContext, TransferState
from rdt.fault_injector import FaultConfig, FaultInjector
from rdt.receiver import StopAndWaitReceiver, TransferError
from rdt.sender import StopAndWaitSender


class RDTTests(unittest.TestCase):
    def transfer(
        self,
        content: bytes,
        with_faults: bool = False,
        send_wrong_transfer: bool = False,
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

        sender_injector = None
        receiver_injector = None
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

    def test_binary_file(self):
        self.transfer(bytes(range(256)) * 20)

    def test_loss_corruption_and_duplicates(self):
        sender, receiver = self.transfer(
            bytes(range(256)) * 40,
            with_faults=True,
        )
        self.assertGreater(sender.statistics.retransmissions, 0)
        self.assertGreater(
            sender.statistics.corrupted_packets
            + receiver.statistics.corrupted_packets,
            0,
        )

    def test_wrong_transfer_id_is_ignored(self):
        self.transfer(b"correct data", send_wrong_transfer=True)

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


if __name__ == "__main__":
    unittest.main()
