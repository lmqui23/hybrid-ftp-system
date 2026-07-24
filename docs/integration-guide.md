# Part A and Part B Integration Guide

## 1. Responsibilities

Part A owns:

- the TCP control connection;
- FTP commands and reply codes;
- authentication and session management;
- Active and Passive mode negotiation;
- file metadata exchange.

Part B owns:

- UDP packet encoding and decoding;
- Stop-and-Wait transmission;
- ACK, timeout, and retransmission;
- duplicate and corruption handling;
- final size and SHA-256 verification;
- transfer cancellation and statistics.

Part B does not import modules from `client`, `server`, or `common`.
Part A connects to Part B through its public classes.

## 2. Information Exchanged Through TCP

Before starting a UDP transfer, both sides must know:

- `transfer_id`: the same unsigned 64-bit value on both sides;
- `size`: the exact number of file bytes;
- `sha256`: the SHA-256 hash of the complete source file;
- the UDP peer address negotiated by `PASV` or `PORT`.

Example preliminary response for a download:

```text
150 TID=12345 SIZE=50000 SHA256=0123456789abcdef...
```

For an upload, the client must send the size and hash to the server
through the TCP control channel before UDP transmission starts.

The exact metadata syntax may be selected by Part A. It must be parsed
consistently by both the client and server.

## 3. Sender API

```python
from rdt.config import RDTConfig
from rdt.context import TransferContext
from rdt.sender import StopAndWaitSender

context = TransferContext(
    transfer_id=transfer_id,
    config=RDTConfig(),
)

sender = StopAndWaitSender(
    sock=udp_socket,
    peer=peer_address,
    context=context,
)

sender.send_file(source_path)
```

`peer_address` is a tuple:

```python
(peer_ip, peer_port)
```

## 4. Receiver API

```python
from rdt.config import RDTConfig
from rdt.context import TransferContext
from rdt.receiver import StopAndWaitReceiver

context = TransferContext(
    transfer_id=transfer_id,
    config=RDTConfig(),
)

receiver = StopAndWaitReceiver(
    sock=udp_socket,
    context=context,
)

receiver.receive_file(
    destination=destination_path,
    expected_size=expected_size,
    expected_hash=expected_sha256,
)
```

The receiver writes to a temporary `.part.<transfer_id>` file. It only
replaces the destination after the size and SHA-256 are correct.

## 5. Mapping Existing Part A Functions

| Part A function | Part B operation |
|---|---|
| `udp_send_file()` | Create a context and call `StopAndWaitSender.send_file()` |
| `udp_receive_file()` | Create a context and call `StopAndWaitReceiver.receive_file()` |
| `udp_abort_transfer()` | Call `context.cancel()` |
| `udp_send_buffer()` | Save the buffer to a temporary file and use the sender |
| `udp_client_send_file()` | Use `StopAndWaitSender` |
| `udp_client_receive_file()` | Use `StopAndWaitReceiver` |

Part A should convert Part B exceptions into suitable FTP replies:

- success: `226 Transfer complete`;
- UDP setup failure: `425 Cannot open data connection`;
- transfer failure or cancellation: `426 Transfer aborted`.

## 6. Passive Mode

1. The client sends `PASV` through TCP.
2. The server binds a UDP socket to an available port.
3. The server returns the IP address and port in reply `227`.
4. The client sends a small UDP readiness datagram to that address.
5. The server obtains the client's UDP address from `recvfrom()`.
6. Both sides create contexts with the same `transfer_id`.
7. The sender and receiver start the transfer.

The readiness datagram is outside the RDT file stream. Its only purpose
is to let a passive UDP server learn the client's address.

## 7. Active Mode

1. The client binds a UDP socket to an available port.
2. The client sends `PORT h1,h2,h3,h4,p1,p2` through TCP.
3. The server stores the client's UDP address.
4. Both sides create contexts with the same `transfer_id`.
5. The server uses the stored address as the sender's `peer`.

If the client will send the file, the server should first send a small
UDP readiness datagram so that the client learns the server's UDP
address.

## 8. Download Workflow

1. The server validates `RETR` and the requested path.
2. The server calculates the file size and SHA-256.
3. The server creates a new `transfer_id`.
4. The server sends the metadata in reply `150`.
5. The client parses the metadata and starts `StopAndWaitReceiver`.
6. The server starts `StopAndWaitSender`.
7. The sender transmits DATA packets and waits for each ACK.
8. The sender sends FIN after the final DATA packet.
9. The receiver validates size and SHA-256.
10. The receiver returns FIN_ACK containing its calculated hash.
11. The server sends reply `226` if the transfer succeeds.

## 9. Upload Workflow

1. The client validates the local file.
2. The client calculates its size and SHA-256.
3. The client sends the upload command and metadata through TCP.
4. The server validates the destination path.
5. Both sides agree on one `transfer_id`.
6. The server starts `StopAndWaitReceiver`.
7. The client starts `StopAndWaitSender`.
8. The server sends `226` after successful size and hash verification.

`STOR`, `APPE`, and `STOU` can reuse this workflow. Part A remains
responsible for selecting the final destination and append behavior.

## 10. ABOR and Concurrency

Part A should store the active context in its FTP session:

```python
session.transfer_context = context
```

To abort:

```python
if session.transfer_context is not None:
    session.transfer_context.cancel()
```

Clear the reference after completion:

```python
session.transfer_context = None
```

The transfer must run in a worker thread. If it runs in the TCP command
loop, that loop cannot receive `ABOR` while waiting for UDP completion.

Each concurrent transfer must have:

- its own `TransferContext`;
- a unique `transfer_id`;
- an isolated UDP socket or correctly isolated UDP routing;
- a separate destination temporary file.

## 11. Integration Order

Integrate and verify features in this order:

1. `PASV` and `RETR`;
2. `PASV` and `STOR`;
3. binary file transfer and SHA-256 comparison;
4. `LIST` and `NLST`;
5. Active mode with `PORT`;
6. `APPE` and `STOU`;
7. `ABOR`, progress display, and concurrent transfers.

The first milestone is a successful binary `PASV + RETR` transfer with
identical file size and SHA-256 on both sides.

## 12. Verification Checklist

- TCP carries commands, replies, size, hash, and transfer ID.
- UDP carries all file payload.
- Sender and receiver use the same `transfer_id`.
- Every DATA packet is acknowledged.
- Lost or corrupted packets cause retransmission.
- Duplicate packets are not written twice.
- Empty and binary files transfer correctly.
- A failed transfer does not replace the destination.
- Temporary files are removed after failure or cancellation.
- `ABOR` cancels the active context.
- Multiple sessions do not share one transfer context.
- Final size and SHA-256 match the source.

Run the Part B tests before integration:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```
