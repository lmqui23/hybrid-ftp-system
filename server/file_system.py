"""Filesystem operations constrained to the configured FTP storage root."""

import os
import time
import hashlib
import stat

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

USERS_FILE = os.path.join(PROJECT_ROOT, "storage", "users.txt")
if not os.path.exists(USERS_FILE):
    USERS_FILE = os.path.join(PROJECT_ROOT, "users.txt")


def verify_user_credentials(username: str, password: str) -> bool:
    if not os.path.exists(USERS_FILE):
        # Mặc định chấp nhận nếu chưa có file users.txt
        return True
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":", 1) if ":" in line else line.split()
                if len(parts) == 2:
                    u, p = parts
                    if u == username and p == password:
                        return True
    except Exception:
        pass
    return False


def is_directory(path: str) -> bool:
    return os.path.isdir(path)


def exists(path: str) -> bool:
    return os.path.exists(path)


def get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return -1


def get_file_mtime(path: str) -> str:
    try:
        mtime = os.path.getmtime(path)
        gm_time = time.gmtime(mtime)
        return time.strftime("%Y%m%d%H%M%S", gm_time)
    except Exception:
        return ""


def calculate_sha256(path: str) -> str:
    sha256_hash = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return ""


def create_directory(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception:
        return False


def remove_directory(path: str) -> bool:
    try:
        os.rmdir(path)
        return True
    except Exception:
        return False


def remove_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except Exception:
        return False


def rename_path(old_path: str, new_path: str) -> bool:
    try:
        os.rename(old_path, new_path)
        return True
    except Exception:
        return False


def generate_unique_filename(base_dir: str, prefix_id: int) -> str:
    timestamp = int(time.time())
    count = 0
    while True:
        name = f"stou_{prefix_id}_{timestamp}_{count}.tmp"
        full_path = os.path.join(base_dir, name)
        if not os.path.exists(full_path):
            return name
        count += 1


def get_directory_listing(dir_path: str) -> str:
    lines = []
    try:
        entries = os.listdir(dir_path)
        entries.sort()
        for name in entries:
            full_path = os.path.join(dir_path, name)
            try:
                st = os.stat(full_path)
                is_dir = stat.S_ISDIR(st.st_mode)
                perm_str = "drwxr-xr-x" if is_dir else "-rw-r--r--"
                nlink = 1
                owner = "owner"
                group = "group"
                size = st.st_size
                mtime_str = time.strftime("%b %d %H:%M", time.localtime(st.st_mtime))
                line = f"{perm_str} {nlink:2d} {owner:8s} {group:8s} {size:8d} {mtime_str} {name}\r\n"
                lines.append(line)
            except Exception:
                continue
    except Exception:
        pass
    return "".join(lines)


def get_simple_listing(dir_path: str) -> str:
    lines = []
    try:
        entries = os.listdir(dir_path)
        entries.sort()
        for name in entries:
            lines.append(f"{name}\r\n")
    except Exception:
        pass
    return "".join(lines)
