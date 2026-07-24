import unittest

from protocol.codec import PacketDecodeError, decode_packet, encode_packet
from protocol.packet import PacketFlag, RDTPacket


class ProtocolTests(unittest.TestCase):
    def test_packet_round_trip(self):
        packet = RDTPacket(
            flags=PacketFlag.DATA | PacketFlag.ACK,
            transfer_id=123,
            sequence_number=7,
            acknowledgment_number=6,
            advertised_window=1,
            payload=b"\x00binary\xff",
        )

        decoded = decode_packet(encode_packet(packet))

        self.assertEqual(decoded.transfer_id, 123)
        self.assertEqual(decoded.sequence_number, 7)
        self.assertEqual(decoded.acknowledgment_number, 6)
        self.assertEqual(decoded.advertised_window, 1)
        self.assertEqual(decoded.flags, PacketFlag.DATA | PacketFlag.ACK)
        self.assertEqual(decoded.payload, b"\x00binary\xff")

    def test_corruption_is_detected(self):
        raw = bytearray(
            encode_packet(RDTPacket(PacketFlag.DATA, 1, payload=b"data"))
        )
        raw[-1] ^= 1

        with self.assertRaises(PacketDecodeError):
            decode_packet(bytes(raw))

    def test_truncated_header_is_rejected(self):
        with self.assertRaises(PacketDecodeError):
            decode_packet(b"RDT1")

    def test_oversized_payload_is_rejected(self):
        packet = RDTPacket(
            PacketFlag.DATA,
            1,
            payload=b"x" * 1025,
        )
        with self.assertRaises(ValueError):
            encode_packet(packet)


if __name__ == "__main__":
    unittest.main()
