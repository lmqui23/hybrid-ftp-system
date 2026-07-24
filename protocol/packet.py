from dataclasses import dataclass, field
from enum import IntFlag

# 0x52445431 = "RDT1"
RDT_MAGIC = 0x52445431

RDT_VERSION = 1

MAX_PAYLOAD_SIZE = 1024

HEADER_SIZE = 32

INITIAL_SEQUENCE_NUMBER = 0


class PacketFlag(IntFlag):
    NONE = 0
    DATA = 1 << 0
    ACK = 1 << 1
    FIN = 1 << 2
    FIN_ACK = 1 << 3
    ERROR = 1 << 4
    CANCEL = 1 << 5


@dataclass(slots=True)
class RDTPacket:
    flags: PacketFlag

    transfer_id: int

    sequence_number: int = INITIAL_SEQUENCE_NUMBER
    acknowledgment_number: int = 0

    advertised_window: int = 1

    payload: bytes = field(default_factory=bytes)

    magic: int = RDT_MAGIC
    version: int = RDT_VERSION

    checksum: int = 0

    @property
    def payload_length(self) -> int:
        return len(self.payload)

    def has_flag(self, flag: PacketFlag) -> bool:
        return bool(self.flags & flag)

    def validate(self) -> None:
        if self.magic != RDT_MAGIC:
            raise ValueError(
                f"Invalid RDT magic: 0x{self.magic:08X}"
            )

        if self.version != RDT_VERSION:
            raise ValueError(
                f"Unsupported RDT version: {self.version}"
            )

        if self.flags == PacketFlag.NONE:
            raise ValueError("Packet must contain at least one flag")

        if not 0 <= self.transfer_id <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError(
                "transfer_id must fit in an unsigned 64-bit integer"
            )

        if not 0 <= self.sequence_number <= 0xFFFFFFFF:
            raise ValueError(
                "sequence_number must fit in an unsigned 32-bit integer"
            )

        if not 0 <= self.acknowledgment_number <= 0xFFFFFFFF:
            raise ValueError(
                "acknowledgment_number must fit in an "
                "unsigned 32-bit integer"
            )

        if not 0 <= self.advertised_window <= 0xFFFF:
            raise ValueError(
                "advertised_window must fit in an "
                "unsigned 16-bit integer"
            )

        if not isinstance(self.payload, bytes):
            raise TypeError("payload must be a bytes object")

        if self.payload_length > MAX_PAYLOAD_SIZE:
            raise ValueError(
                f"Payload is too large: {self.payload_length} bytes; "
                f"maximum is {MAX_PAYLOAD_SIZE} bytes"
            )

        if not 0 <= self.checksum <= 0xFFFFFFFF:
            raise ValueError(
                "checksum must fit in an unsigned 32-bit integer"
            )
