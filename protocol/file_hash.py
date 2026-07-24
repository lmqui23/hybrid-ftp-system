import hashlib
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()

    with Path(path).open("rb") as file:
        while chunk := file.read(64 * 1024):
            digest.update(chunk)

    return digest.hexdigest()
