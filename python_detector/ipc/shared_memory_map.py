from __future__ import annotations

import ctypes
import mmap
import os
import sys
from dataclasses import dataclass
from multiprocessing import shared_memory


def platform_shared_memory_name(name: str) -> str:
    """Map the protocol-level shared-memory name to the current OS namespace."""
    if sys.platform == "win32":
        if name.startswith(("Local\\", "Global\\")):
            return name
        return "Local\\" + name.replace("/", "")
    return name


@dataclass
class SharedMemoryMap:
    name: str
    size: int
    mm: mmap.mmap | memoryview
    fd: int | None = None
    shm: shared_memory.SharedMemory | None = None

    @classmethod
    def open(cls, name: str, size: int) -> "SharedMemoryMap":
        mapped_name = platform_shared_memory_name(name)
        if sys.platform == "win32":
            try:
                shm = shared_memory.SharedMemory(name=mapped_name, create=False)
            except OSError as exc:
                raise FileNotFoundError(exc.errno, str(exc), mapped_name) from exc
            if shm.size < size:
                shm.close()
                raise RuntimeError(f"shared memory size mismatch: name={mapped_name} size={shm.size} expected={size}")
            return cls(name=mapped_name, size=size, fd=None, mm=shm.buf[:size].cast("B"), shm=shm)

        libc = ctypes.CDLL(None, use_errno=True)
        shm_open = libc.shm_open
        shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
        shm_open.restype = ctypes.c_int
        fd = shm_open(mapped_name.encode("utf-8"), os.O_RDWR, 0)
        if fd < 0:
            errno = ctypes.get_errno()
            raise FileNotFoundError(errno, os.strerror(errno), mapped_name)
        try:
            mm = mmap.mmap(fd, size)
        except Exception:
            os.close(fd)
            raise
        return cls(name=mapped_name, size=size, fd=fd, mm=mm)

    def close(self) -> None:
        if isinstance(self.mm, memoryview):
            self.mm.release()
        else:
            self.mm.close()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.shm is not None:
            self.shm.close()
            self.shm = None
