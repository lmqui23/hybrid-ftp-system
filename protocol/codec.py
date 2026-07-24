import struct

from protocol.crc32 import calculate_crc32
from protocol.packet import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    RDT_MAGIC,
    RDT_VERSION,
    PacketFlag,
    RDTPacket,
)


HEADER_FORMAT = "!IBBHQIIHHI"
HEADER_STRUCT = struct.Struct(HEADER_FORMAT)


KNOWN_FLAGS = (
    PacketFlag.DATA
    | PacketFlag.ACK
    | PacketFlag.FIN
    | PacketFlag.FIN_ACK
    | PacketFlag.ERROR
    | PacketFlag.CANCEL
)

KNOWN_FLAGS_VALUE = int(KNOWN_FLAGS)


class PacketEncodeError(ValueError):
    pass


class PacketDecodeError(ValueError):
    pass

def _validate_header_size() -> None:
    if HEADER_STRUCT.size != HEADER_SIZE:
        raise RuntimeError(
            "RDT header configuration is inconsistent: "
            f"struct size={HEADER_STRUCT.size}, "
            f"declared size={HEADER_SIZE}"
        )


def _validate_flags_value(flags_value: int) -> None:
    unknown_bits = flags_value & ~KNOWN_FLAGS_VALUE

    if unknown_bits != 0:
        raise PacketDecodeError(
            f"Packet contains unsupported flag bits: "
            f"0x{unknown_bits:02X}"
        )

    if flags_value == int(PacketFlag.NONE):
        raise PacketDecodeError(
            "Packet must contain at least one flag"
        )


def _pack_header(
    *,
    magic: int,
    version: int,
    flags: int,
    header_size: int,
    transfer_id: int,
    sequence_number: int,
    acknowledgment_number: int,
    advertised_window: int,
    payload_length: int,
    checksum: int,
) -> bytes:
    return HEADER_STRUCT.pack(
        magic,
        version,
        flags,
        header_size,
        transfer_id,
        sequence_number,
        acknowledgment_number,
        advertised_window,
        payload_length,
        checksum,
    )


def encode_packet(packet: RDTPacket) -> bytes:
    _validate_header_size()

    if not isinstance(packet, RDTPacket):
        raise TypeError("packet must be an RDTPacket instance")

    try:
        packet.validate()
    except (TypeError, ValueError) as error:
        raise PacketEncodeError(
            f"Invalid packet: {error}"
        ) from error

    flags_value = int(packet.flags)

    unknown_bits = flags_value & ~KNOWN_FLAGS_VALUE

    if unknown_bits != 0:
        raise PacketEncodeError(
            f"Packet contains unsupported flag bits: "
            f"0x{unknown_bits:02X}"
        )

    payload_length = packet.payload_length

    try:
        header_with_zero_checksum = _pack_header(
            magic=packet.magic,
            version=packet.version,
            flags=flags_value,
            header_size=HEADER_SIZE,
            transfer_id=packet.transfer_id,
            sequence_number=packet.sequence_number,
            acknowledgment_number=(
                packet.acknowledgment_number
            ),
            advertised_window=packet.advertised_window,
            payload_length=payload_length,
            checksum=0,
        )
    except struct.error as error:
        raise PacketEncodeError(
            f"Header field does not fit wire format: {error}"
        ) from error

    checksum_input = (
        header_with_zero_checksum
        + packet.payload
    )

    checksum = calculate_crc32(checksum_input)

    try:
        final_header = _pack_header(
            magic=packet.magic,
            version=packet.version,
            flags=flags_value,
            header_size=HEADER_SIZE,
            transfer_id=packet.transfer_id,
            sequence_number=packet.sequence_number,
            acknowledgment_number=(
                packet.acknowledgment_number
            ),
            advertised_window=packet.advertised_window,
            payload_length=payload_length,
            checksum=checksum,
        )
    except struct.error as error:
        raise PacketEncodeError(
            f"Unable to encode final header: {error}"
        ) from error

    packet.checksum = checksum

    return final_header + packet.payload


def decode_packet(datagram: bytes) -> RDTPacket:
    _validate_header_size()

    if not isinstance(datagram, bytes):
        raise TypeError("datagram must be a bytes object")

    if len(datagram) < HEADER_SIZE:
        raise PacketDecodeError(
            f"Datagram is too short: received {len(datagram)} "
            f"bytes, minimum is {HEADER_SIZE}"
        )

    header_bytes = datagram[:HEADER_SIZE]

    try:
        (
            magic,
            version,
            flags_value,
            header_size,
            transfer_id,
            sequence_number,
            acknowledgment_number,
            advertised_window,
            payload_length,
            received_checksum,
        ) = HEADER_STRUCT.unpack(header_bytes)
    except struct.error as error:
        raise PacketDecodeError(
            f"Unable to unpack RDT header: {error}"
        ) from error

    if magic != RDT_MAGIC:
        raise PacketDecodeError(
            f"Invalid RDT magic: 0x{magic:08X}"
        )

    if version != RDT_VERSION:
        raise PacketDecodeError(
            f"Unsupported RDT version: {version}"
        )

    if header_size != HEADER_SIZE:
        raise PacketDecodeError(
            f"Invalid header size: {header_size}; "
            f"expected {HEADER_SIZE}"
        )

    _validate_flags_value(flags_value)

    if payload_length > MAX_PAYLOAD_SIZE:
        raise PacketDecodeError(
            f"Payload is too large: {payload_length}; "
            f"maximum is {MAX_PAYLOAD_SIZE}"
        )

    expected_datagram_length = (
        header_size + payload_length
    )

    if len(datagram) != expected_datagram_length:
        raise PacketDecodeError(
            "Datagram length does not match payload_length: "
            f"received={len(datagram)}, "
            f"expected={expected_datagram_length}"
        )

    payload = datagram[header_size:]


    header_with_zero_checksum = _pack_header(
        magic=magic,
        version=version,
        flags=flags_value,
        header_size=header_size,
        transfer_id=transfer_id,
        sequence_number=sequence_number,
        acknowledgment_number=acknowledgment_number,
        advertised_window=advertised_window,
        payload_length=payload_length,
        checksum=0,
    )

    calculated_checksum = calculate_crc32(
        header_with_zero_checksum + payload
    )

    if calculated_checksum != received_checksum:
        raise PacketDecodeError(
            "CRC32 mismatch: "
            f"received=0x{received_checksum:08X}, "
            f"calculated=0x{calculated_checksum:08X}"
        )

    packet = RDTPacket(
        flags=PacketFlag(flags_value),
        transfer_id=transfer_id,
        sequence_number=sequence_number,
        acknowledgment_number=acknowledgment_number,
        advertised_window=advertised_window,
        payload=payload,
        magic=magic,
        version=version,
        checksum=received_checksum,
    )

    try:
        packet.validate()
    except (TypeError, ValueError) as error:
        raise PacketDecodeError(
            f"Decoded packet is invalid: {error}"
        ) from error

    return packet
