"""Barreira central de escrita financeira no SOMA.

Todo POST que altera dados do SOMA passa por aqui (ResilientSession.post). Sem
autorização explícita do ledger, ou com PAUSA_FINANCEIRA presente, o pedido é
recusado antes de sair da máquina. Cobre orquestrador, scripts, ronda e qualquer
código que use o cliente HTTP do projeto.

Leituras (sys/post/*, buscas, GET das páginas e login em /apps/sys/*) não são afetadas.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

_AUTORIZADA: contextvars.ContextVar[bool] = contextvars.ContextVar("escrita_financeira_autorizada", default=False)


class EscritaFinanceiraBloqueada(RuntimeError):
    """Pedido de escrita financeira recusado pela barreira."""


@contextmanager
def escrita_autorizada_pelo_ledger() -> Iterator[None]:
    """Usar apenas no caminho de envio do orquestrador, depois de registar ENVIANDO no ledger."""
    token = _AUTORIZADA.set(True)
    try:
        yield
    finally:
        _AUTORIZADA.reset(token)


def eh_escrita_financeira(url: str, data: Any = None) -> bool:
    url_n = (url or "").lower()
    if "/sys/app/" in url_n or "faz=dados" in url_n:
        return True
    if isinstance(data, dict) and "excluir" in data:
        return True
    return False


def _pausa_path() -> Path:
    from core.operation_ledger import resolve_ledger_path

    return resolve_ledger_path().parent / "PAUSA_FINANCEIRA"


def verificar_escrita(url: str, data: Any = None, pausa: Optional[Path] = None) -> None:
    """Levanta EscritaFinanceiraBloqueada se o pedido for escrita financeira não autorizada."""
    if not eh_escrita_financeira(url, data):
        return
    pausa = pausa or _pausa_path()
    if pausa.exists():
        raise EscritaFinanceiraBloqueada(f"escrita financeira recusada: pausa ativa ({pausa})")
    if not _AUTORIZADA.get():
        raise EscritaFinanceiraBloqueada(f"escrita financeira recusada sem autorização do ledger: {url}")
