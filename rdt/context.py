import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from rdt.config import RDTConfig


@dataclass
class TransferStatistics:
    bytes_transferred: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    acknowledgments_sent: int = 0
    acknowledgments_received: int = 0
    duplicate_packets: int = 0
    corrupted_packets: int = 0
    duration_seconds: float = 0.0


class TransferState(Enum):
    CREATED = "created"
    NEGOTIATING = "negotiating"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferContext:
    transfer_id: int
    config: RDTConfig

    state: TransferState = TransferState.CREATED
    statistics: TransferStatistics = field(
        default_factory=TransferStatistics
    )

    cancel_event: threading.Event = field(
        default_factory=threading.Event
    )

    started_at: float = field(
        default_factory=time.monotonic
    )

    def cancel(self) -> None:
        self.cancel_event.set()
        self.state = TransferState.CANCELLED

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def finish(self, state: TransferState) -> None:
        self.state = state
        self.statistics.duration_seconds = (
            time.monotonic() - self.started_at
        )
