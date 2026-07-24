# Excellent-Level Demo Guide

## 1. Start and verify

Run the automated suite:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

The suite covers packet encoding, corruption, binary and empty files,
loss/corruption/duplicates, total packet loss, transfer isolation, safe
hash failure, PASV/PORT, STOR/RETR, NLST, APPE, STOU, HELP, TCP framing,
two clients, and live ABOR.

Start the application:

```powershell
python server/tcp_control/tcp_server.py
python client/tcp_client/control_client.py
```

## 2. Recommended command sequence

```text
USER admin
PASS 123456
PWD
MKD demo
CWD demo
STOR sample.bin
HASH sample.bin
PORT
RETR sample.bin
NLST
HELP RETR
QUIT
```

Place `sample.bin` in `storage/client_files` before the demo. A data
command automatically selects PASV when no data mode has been selected.
Enter `PORT` explicitly before a transfer to demonstrate Active mode.

## 3. Fault-injection demonstration

Set fault rates before starting both server and client:

```powershell
$env:RDT_LOSS_RATE = "0.10"
$env:RDT_CORRUPTION_RATE = "0.05"
$env:RDT_DUPLICATE_RATE = "0.05"
```

Run a transfer. The client reports bytes, percentage, retransmissions,
and duration. Verify that retransmissions are greater than zero and the
final SHA-256 still matches.

Clear the variables after the demo:

```powershell
Remove-Item Env:RDT_LOSS_RATE
Remove-Item Env:RDT_CORRUPTION_RATE
Remove-Item Env:RDT_DUPLICATE_RATE
```

## 4. ABOR demonstration

Transfer a large file. While progress is below 100%, enter:

```text
ABOR
```

Expected evidence:

- server reply `426 Transfer aborted`;
- client transfer thread stops;
- no incomplete destination replaces a valid file;
- no `.part.<transfer_id>` file remains.

## 5. Concurrency demonstration

Open two clients and authenticate both. Start a large transfer in the
first client and issue `PWD`, `LIST`, or another transfer in the second.
The server prints an active-session table with client address, user,
mode, and transfer state.

## 6. Evidence to capture

Capture screenshots or terminal logs for:

1. authentication and connected-client table;
2. PASV upload and Active download;
3. SHA-256 equality;
4. fault injection with retransmissions;
5. live ABOR and temporary-file cleanup;
6. two simultaneous client sessions;
7. all automated tests passing.

## 7. Viva points

- Stop-and-Wait is a sender window of one packet.
- At most one DATA packet is unacknowledged, preventing flooding.
- CRC32 detects datagram corruption; SHA-256 verifies the whole file.
- Transfer ID rejects stale packets from another transfer.
- A duplicate DATA packet is acknowledged again but not written twice.
- Approximate utilization is `(L/R) / (RTT + L/R)`.
- Go-Back-N retransmits from the missing packet onward.
- Selective Repeat retransmits only missing or corrupted packets.
