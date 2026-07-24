from dataclasses import dataclass

from protocol.packet import MAX_PAYLOAD_SIZE


@dataclass(frozen=True)
class RDTConfig:
    payload_size: int = MAX_PAYLOAD_SIZE
    retransmission_timeout: float = 0.5
    idle_timeout: float = 10.0
    maximum_retries: int = 10
    receive_poll_interval: float = 0.2

    def __post_init__(self) -> None:
        if not 1 <= self.payload_size <= MAX_PAYLOAD_SIZE:
            raise ValueError(
                f"payload_size must be between 1 and {MAX_PAYLOAD_SIZE}"
            )
        if self.retransmission_timeout <= 0:
            raise ValueError("retransmission_timeout must be positive")
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        if self.maximum_retries < 0:
            raise ValueError("maximum_retries cannot be negative")
        if self.receive_poll_interval <= 0:
            raise ValueError("receive_poll_interval must be positive")
