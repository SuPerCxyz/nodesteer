#!/usr/bin/env python3
"""QA payload: two tagged streams per attempt; fail twice, succeed once."""
import os
import hashlib
import stat
import sys
import time
from pathlib import Path

NONCE = "b7e13d4c9a21"
PARAM_KEY = "NODESTEER_PARAM_qa_value"
PARAM_VALUE = NONCE
FIXED_STATE = Path("/tmp/nodesteer-qa-sys34-40-b7e13d4c9a21.state")
CHUNK_BYTES = 70 * 1024
MODE = 0o600
STATE_1 = (NONCE + ":1").encode("ascii")
STATE_2 = (NONCE + ":2").encode("ascii")
MAX_STATE_BYTES = max(len(STATE_1), len(STATE_2))
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class StateGuardError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def write_all(fd: int, data: bytes, writer=os.write) -> None:
    view = memoryview(data)
    while view:
        written = writer(fd, view)
        if written <= 0:
            raise StateGuardError("stream_write_failed")
        view = view[written:]


def checked_stat(path: Path, expected=None):
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != MODE
            or st.st_uid != os.geteuid() or st.st_gid != os.getegid()
            or st.st_nlink != 1 or st.st_size > MAX_STATE_BYTES):
        raise StateGuardError("marker_owner_type_mode_or_size_guard_failed")
    if expected is not None and (st.st_dev, st.st_ino) != expected:
        raise StateGuardError("marker_inode_changed")
    return st


def create_marker(path: Path) -> tuple[int, int]:
    if not NOFOLLOW:
        raise StateGuardError("nofollow_unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW
    try:
        fd = os.open(path, flags, MODE)
    except FileExistsError:
        raise StateGuardError("marker_collision_refused")
    except OSError:
        raise StateGuardError("marker_create_failed")
    try:
        os.fchmod(fd, MODE)
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != MODE
                or st.st_uid != os.geteuid() or st.st_gid != os.getegid()
                or st.st_nlink != 1 or st.st_size != 0):
            raise StateGuardError("new_marker_guard_failed")
        key = (st.st_dev, st.st_ino)
        write_all(fd, STATE_1)
        os.fsync(fd)
        after = os.fstat(fd)
        path_st = checked_stat(path, key)
        if (path_st is None or (after.st_dev, after.st_ino) != key
                or after.st_size != len(STATE_1)):
            raise StateGuardError("new_marker_identity_changed")
        return key
    except OSError:
        raise StateGuardError("new_marker_write_failed")
    finally:
        os.close(fd)


def read_marker(path: Path) -> tuple[int, tuple[int, int]]:
    before = checked_stat(path)
    if before is None:
        return 0, create_marker(path)
    key = (before.st_dev, before.st_ino)
    try:
        fd = os.open(path, os.O_RDONLY | NOFOLLOW)
    except OSError:
        raise StateGuardError("marker_open_read_failed")
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != key or not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != MODE or opened.st_uid != os.geteuid()
                or opened.st_gid != os.getegid() or opened.st_nlink != 1
                or opened.st_size > MAX_STATE_BYTES):
            raise StateGuardError("marker_read_fd_guard_failed")
        data = os.read(fd, MAX_STATE_BYTES + 1)
        if data not in (STATE_1, STATE_2):
            raise StateGuardError("marker_content_rejected")
        if os.fstat(fd).st_size != len(data):
            raise StateGuardError("marker_size_changed_during_read")
        checked_stat(path, key)
        return (1 if data == STATE_1 else 2), key
    except OSError:
        raise StateGuardError("marker_read_failed")
    finally:
        os.close(fd)


def update_marker_to_two(path: Path, key: tuple[int, int]) -> None:
    checked_stat(path, key)
    try:
        fd = os.open(path, os.O_RDWR | NOFOLLOW)
    except OSError:
        raise StateGuardError("marker_open_write_failed")
    try:
        st = os.fstat(fd)
        if ((st.st_dev, st.st_ino) != key or not stat.S_ISREG(st.st_mode)
                or stat.S_IMODE(st.st_mode) != MODE or st.st_uid != os.geteuid()
                or st.st_gid != os.getegid() or st.st_nlink != 1
                or st.st_size != len(STATE_1)):
            raise StateGuardError("marker_write_fd_guard_failed")
        if os.pread(fd, MAX_STATE_BYTES + 1, 0) != STATE_1:
            raise StateGuardError("marker_content_rejected")
        checked_stat(path, key)
        os.ftruncate(fd, 0)
        write_all(fd, STATE_2)
        os.fsync(fd)
        after = os.fstat(fd)
        if ((after.st_dev, after.st_ino) != key or after.st_size != len(STATE_2)
                or checked_stat(path, key) is None):
            raise StateGuardError("marker_update_identity_changed")
    except OSError:
        raise StateGuardError("marker_update_failed")
    finally:
        os.close(fd)


def remove_marker(path: Path, key: tuple[int, int]) -> None:
    st = checked_stat(path, key)
    if st is None:
        raise StateGuardError("marker_missing_before_unlink")
    try:
        fd = os.open(path, os.O_RDONLY | NOFOLLOW)
    except OSError:
        raise StateGuardError("marker_open_before_unlink_failed")
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != key or not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != MODE or opened.st_uid != os.geteuid()
                or opened.st_gid != os.getegid() or opened.st_nlink != 1):
            raise StateGuardError("marker_unlink_fd_guard_failed")
        if os.read(fd, MAX_STATE_BYTES + 1) != STATE_2:
            raise StateGuardError("marker_content_rejected_before_unlink")
        if checked_stat(path, key) is None:
            raise StateGuardError("marker_missing_before_unlink")
        os.unlink(path)
        if os.path.lexists(path):
            raise StateGuardError("marker_still_present_after_unlink")
    except OSError:
        raise StateGuardError("marker_unlink_failed")
    finally:
        os.close(fd)


def run(state_path: Path, writer=os.write) -> int:
    attempt_start_ns = time.monotonic_ns()
    parameter = os.environ.get(PARAM_KEY, "")
    if parameter != PARAM_VALUE:
        write_all(2, f"QA:{NONCE}:SAFE_ERROR:required_parameter_mismatch\n".encode(), writer)
        return 89
    parameter_digest = hashlib.sha256(parameter.encode("utf-8")).hexdigest()
    parameter_facts = f"param_len={len(parameter)}:param_sha256={parameter_digest}"
    try:
        previous, key = read_marker(state_path)
        attempt = previous + 1
        if attempt not in (1, 2, 3):
            raise StateGuardError("unexpected_attempt_number")
        if attempt == 2:
            update_marker_to_two(state_path, key)
    except StateGuardError as exc:
        write_all(2, f"QA:{NONCE}:SAFE_ERROR:{exc.code}\n".encode(), writer)
        return 90

    for fd, stream, fill in ((1, "stdout", b"O"), (2, "stderr", b"E")):
        begin = (f"QA:{NONCE}:attempt={attempt}:tmono_ns={attempt_start_ns}:{parameter_facts}:"
                 f"stream={stream}:BEGIN\n").encode()
        end = f"\nQA:{NONCE}:attempt={attempt}:stream={stream}:END\n".encode()
        payload = begin + fill * (CHUNK_BYTES - len(begin) - len(end)) + end
        try:
            write_all(fd, payload, writer)
        except (OSError, StateGuardError):
            return 91
    if attempt < 3:
        return 23
    try:
        remove_marker(state_path, key)
    except StateGuardError as exc:
        write_all(2, f"QA:{NONCE}:SAFE_ERROR:{exc.code}\n".encode(), writer)
        return 92
    write_all(1, f"QA:{NONCE}:attempt=3:STATE_REMOVED\n".encode(), writer)
    return 0


def main() -> int:
    return run(FIXED_STATE)


if __name__ == "__main__":
    sys.exit(main())
