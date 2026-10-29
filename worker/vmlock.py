"""Creneau de telechargement UNIQUE pour toute la VM, partage par les deux workers
(mathou/cdlr et anime-worker) : un seul job a la fois, tous outils confondus.

Pourquoi : la VM n'a que 4 Go de RAM. cdlr (+ son Chrome) et Anime-Downloader en
parallele la saturent (CPU 100 %, cdlr plante en "TLS handshake timeout"), et le
PRE_JOB_KILL de chaque worker tue le ffmpeg de l'autre.

Mutex Windows nomme (meme nom dans les deux workers) : si le process qui le tient
meurt, l'OS le libere (WAIT_ABANDONED = on l'obtient quand meme) -> jamais de verrou
fantome. Un mutex appartient a un THREAD : acquerir et liberer dans le meme thread."""
from __future__ import annotations

import contextlib
import sys
from typing import Callable, Iterator

_NAME = "Global\\mathou-vm-download-slot"
_INFINITE = 0xFFFFFFFF
_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF


@contextlib.contextmanager
def download_slot(on_wait: Callable[[], None] | None = None) -> Iterator[None]:
    """Bloque jusqu'a ce que le creneau soit libre. on_wait() est appele une fois si on
    doit attendre (l'autre worker telecharge deja)."""
    if sys.platform != "win32":
        yield
        return
    import ctypes
    from ctypes import wintypes

    # instance privee : ne modifie pas les argtypes de ctypes.windll.kernel32 (run.py)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = k32.CreateMutexW(None, False, _NAME)
    if not handle:
        # pas de mutex possible : on ne bloque pas la file pour autant
        yield
        return
    try:
        r = k32.WaitForSingleObject(handle, 0)
        if r == _WAIT_TIMEOUT:
            if on_wait:
                on_wait()
            r = k32.WaitForSingleObject(handle, _INFINITE)
        owned = r != _WAIT_FAILED
        try:
            yield
        finally:
            if owned:
                k32.ReleaseMutex(handle)
    finally:
        k32.CloseHandle(handle)
