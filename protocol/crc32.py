import zlib


CRC32_MASK = 0xFFFFFFFF


def calculate_crc32(data: bytes) -> int:
    if not isinstance(data, bytes):
        raise TypeError("CRC32 input must be a bytes object")

    return zlib.crc32(data) & CRC32_MASK


def verify_crc32(data: bytes, expected_checksum: int) -> bool:
    if not isinstance(data, bytes):
        raise TypeError("CRC32 input must be a bytes object")

    if not 0 <= expected_checksum <= CRC32_MASK:
        raise ValueError(
            "expected_checksum must fit in an unsigned 32-bit integer"
        )

    return calculate_crc32(data) == expected_checksum
