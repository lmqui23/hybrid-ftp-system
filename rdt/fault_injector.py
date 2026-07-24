import random
import socket
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class FaultConfig:
    loss_rate: float = 0.0
    corruption_rate: float = 0.0
    duplicate_rate: float = 0.0
    seed: int | None = None


class FaultInjector:
    def __init__(self, config: FaultConfig | None = None):
        self.config = config or FaultConfig()
        for name in ("loss_rate", "corruption_rate", "duplicate_rate"):
            value = getattr(self.config, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        self.random = random.Random(self.config.seed)
        self.lock = threading.Lock()

    def sendto(
        self,
        sock: socket.socket,
        data: bytes,
        address: tuple[str, int],
    ) -> int:
        with self.lock:
            if self.random.random() < self.config.loss_rate:
                return len(data)

            output = data

            if (
                output
                and self.random.random()
                < self.config.corruption_rate
            ):
                output = bytearray(output)
                index = self.random.randrange(len(output))
                output[index] ^= 1
                output = bytes(output)

            sent = sock.sendto(output, address)

            if (
                self.random.random()
                < self.config.duplicate_rate
            ):
                sock.sendto(output, address)

            return sent
