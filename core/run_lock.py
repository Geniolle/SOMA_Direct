from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config.paths import PROJECT_ROOT

logger = logging.getLogger("soma_direct.lock")

DEFAULT_LOCK_PATH = PROJECT_ROOT / ".orchestrator.lock"


def _try_lock(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def orchestrator_session(lock_path: Path = DEFAULT_LOCK_PATH) -> Iterator[bool]:
    """Garante uma única sessão ativa do orquestrador (agendador ou manual).

    Produz True quando esta execução obteve a sessão; False quando outra já está
    ativa. O lock é do sistema operativo: é libertado mesmo se o processo morrer.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    fh = open(lock_path, "r+")
    try:
        if not _try_lock(fh):
            try:
                fh.seek(0)
                owner = fh.read().strip() or "desconhecido"
            except OSError:
                owner = "desconhecido"
            logger.warning(
                "O orquestrador já tem uma sessão ativa (PID %s). Execução abortada.", owner
            )
            yield False
            return
        try:
            try:
                fh.seek(0)
                fh.write(f"{os.getpid():<20}")
                fh.flush()
            except OSError:
                pass
            yield True
        finally:
            _unlock(fh)
    finally:
        fh.close()
