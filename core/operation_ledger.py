"""Ledger durável de operações financeiras no SOMA.

Chave estável: PROCESSO|ID_INTERNO. Nunca o número da linha, que muda quando a
CONTAORDEM é reordenada. Sem ID_INTERNO a operação não é identificável e é recusada.

Fases de cada operação:
- PREPARAÇÃO: validação e resolução de catálogos. Falha aqui não gera registo.
- INTENÇÃO_PERSISTIDA: registo ENVIANDO gravado (commit síncrono) antes de qualquer POST.
- ENVIO_INICIADO: pedido enviado. Desde este ponto, nenhuma exceção genérica é NAO_CRIADO.
- RESULTADO_CONFIRMADO: CONFIRMADO, com DOC comprovado.
- RESULTADO_DESCONHECIDO: sem confirmação conclusiva. Bloqueia até reconciliação.

NAO_CRIADO só é gravado para recusa explícita do SOMA (status JSON diferente de 1),
que é um resultado conclusivo.

Ledger fora da árvore do código (SOMA_LEDGER_PATH ou diretório de dados do sistema),
para sobreviver a deploys e rollbacks. Ausente, corrompido, sem permissões seguras ou
inacessível: LedgerError e nenhum envio.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ENVIANDO = "ENVIANDO"
CONFIRMADO = "CONFIRMADO"
RESULTADO_DESCONHECIDO = "RESULTADO_DESCONHECIDO"
NAO_CRIADO = "NAO_CRIADO"

_BLOQUEANTES = (ENVIANDO, CONFIRMADO, RESULTADO_DESCONHECIDO)
_RECONCILIAVEIS = (ENVIANDO, RESULTADO_DESCONHECIDO)
_DESTINOS_RECONCILIACAO = (CONFIRMADO, NAO_CRIADO)
_RESULTADOS = (CONFIRMADO, RESULTADO_DESCONHECIDO, NAO_CRIADO)
_NOME_FICHEIRO = "operacoes_financeiras.sqlite3"


class LedgerError(RuntimeError):
    """Ledger ausente, corrompido, inacessível ou transição inválida. Nunca enviar POST."""


class IdentificacaoInsuficiente(LedgerError):
    """Linha sem PROCESSO ou ID_INTERNO: não é possível garantir idempotência."""


@dataclass(frozen=True)
class Registo:
    chave: str
    estado: str
    doc_id: str
    tentativas: int


def resolve_ledger_path() -> Path:
    env = os.environ.get("SOMA_LEDGER_PATH", "").strip()
    if env:
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "SOMA_Direct" / "ledger" / _NOME_FICHEIRO
    return Path("/var/lib/soma_direct/ledger") / _NOME_FICHEIRO


def chave_operacao(processo: str, id_interno: str) -> str:
    processo_n = (processo or "").strip().upper()
    id_n = (id_interno or "").strip()
    if not processo_n or not id_n:
        raise IdentificacaoInsuficiente(
            f"identificação insuficiente para lançamento financeiro (PROCESSO='{processo or ''}', ID_INTERNO='{id_interno or ''}')"
        )
    return f"{processo_n}|{id_n}"


class OperationLedger:
    def __init__(self, path: Path | str, criar: bool = False):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        if not self.path.exists():
            if not criar:
                raise LedgerError(f"ledger ausente em {self.path}: inicializar explicitamente antes de lançar")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if os.name != "nt":
                    os.chmod(self.path.parent, 0o700)
            except OSError as exc:
                raise LedgerError(f"diretório do ledger inacessível ({self.path.parent}): {exc}") from exc
        self._verificar_permissoes(criar)
        try:
            self._conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            resultado = self._conn.execute("PRAGMA quick_check").fetchone()
            if not resultado or resultado[0] != "ok":
                raise LedgerError(f"ledger corrompido em {self.path}: {resultado}")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS operacoes ("
                " chave TEXT PRIMARY KEY, estado TEXT NOT NULL, doc_id TEXT NOT NULL DEFAULT '',"
                " tentativas INTEGER NOT NULL DEFAULT 0, atualizado REAL NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS eventos ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT NOT NULL, ts REAL NOT NULL,"
                " estado TEXT NOT NULL, detalhe TEXT NOT NULL DEFAULT '')"
            )
        except LedgerError:
            self.close()
            raise
        except sqlite3.Error as exc:
            self.close()
            raise LedgerError(f"ledger inacessível ou corrompido em {self.path}: {exc}") from exc
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    def _verificar_permissoes(self, criar: bool) -> None:
        if os.name == "nt" or not self.path.exists():
            return
        modo = stat.S_IMODE(self.path.stat().st_mode)
        if modo & 0o077:
            raise LedgerError(f"permissões inseguras no ledger ({oct(modo)}); esperado 0600")

    @property
    def pausa_path(self) -> Path:
        """Ficheiro de pausa financeira, ao lado do ledger. Existir = nenhum POST financeiro."""
        return self.path.parent / "PAUSA_FINANCEIRA"

    def pausa_ativa(self) -> bool:
        return self.pausa_path.exists()

    @classmethod
    def inicializar(cls, path: Path | str) -> "OperationLedger":
        return cls(path, criar=True)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- execução transacional -------------------------------------------

    def _tx(self, operacao):
        if self._conn is None:
            raise LedgerError("ledger fechado")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                resultado = operacao(self._conn)
                self._conn.execute("COMMIT")
                return resultado
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        except LedgerError:
            raise
        except sqlite3.Error as exc:
            raise LedgerError(f"falha no ledger: {exc}") from exc

    def _ler(self, operacao):
        if self._conn is None:
            raise LedgerError("ledger fechado")
        try:
            return operacao(self._conn)
        except sqlite3.Error as exc:
            raise LedgerError(f"falha de leitura no ledger: {exc}") from exc

    @staticmethod
    def _evento(conn: sqlite3.Connection, chave: str, estado: str, detalhe: str) -> None:
        conn.execute(
            "INSERT INTO eventos (chave, ts, estado, detalhe) VALUES (?, ?, ?, ?)",
            (chave, time.time(), estado, detalhe[:500]),
        )

    @staticmethod
    def _obter(conn: sqlite3.Connection, chave: str) -> Optional[Registo]:
        row = conn.execute(
            "SELECT chave, estado, doc_id, tentativas FROM operacoes WHERE chave = ?", (chave,)
        ).fetchone()
        return Registo(*row) if row else None

    # --- API pública ----------------------------------------------------------

    def obter(self, chave: str) -> Optional[Registo]:
        return self._ler(lambda conn: self._obter(conn, chave))

    def bloqueado(self, chave: str) -> bool:
        reg = self.obter(chave)
        return bool(reg and reg.estado in _BLOQUEANTES)

    def registar_envio(self, chave: str) -> Registo:
        """INTENÇÃO_PERSISTIDA: grava ENVIANDO antes do POST. Recusa operação bloqueada."""
        def op(conn):
            reg = self._obter(conn, chave)
            if reg and reg.estado in _BLOQUEANTES:
                raise LedgerError(f"operação {chave} bloqueada: estado {reg.estado}")
            tentativas = (reg.tentativas if reg else 0) + 1
            conn.execute(
                "INSERT INTO operacoes (chave, estado, doc_id, tentativas, atualizado) VALUES (?, ?, '', ?, ?)"
                " ON CONFLICT(chave) DO UPDATE SET estado=excluded.estado, doc_id='',"
                " tentativas=excluded.tentativas, atualizado=excluded.atualizado",
                (chave, ENVIANDO, tentativas, time.time()),
            )
            self._evento(conn, chave, ENVIANDO, f"tentativa {tentativas}")
            return Registo(chave, ENVIANDO, "", tentativas)
        return self._tx(op)

    def registar_resultado(self, chave: str, estado: str, doc_id: str = "", detalhe: str = "") -> None:
        if estado not in _RESULTADOS:
            raise LedgerError(f"estado inválido: {estado}")
        if estado == CONFIRMADO and not doc_id.strip():
            raise LedgerError("CONFIRMADO exige doc_id")

        def op(conn):
            reg = self._obter(conn, chave)
            if reg is None or reg.estado != ENVIANDO:
                raise LedgerError(f"resultado sem envio ativo para {chave}")
            conn.execute(
                "UPDATE operacoes SET estado=?, doc_id=?, atualizado=? WHERE chave=?",
                (estado, doc_id.strip(), time.time(), chave),
            )
            self._evento(conn, chave, estado, detalhe or doc_id)
        self._tx(op)

    def importar_historico(self, chave: str, estado: str, motivo: str, doc_id: str = "") -> None:
        """Inicialização manual: regista operação já existente no SOMA e sem registo no ledger.

        Só CONFIRMADO (com DOC) ou RESULTADO_DESCONHECIDO. Recusa chave que já exista.
        """
        if estado not in (CONFIRMADO, RESULTADO_DESCONHECIDO):
            raise LedgerError("importação só aceita CONFIRMADO ou RESULTADO_DESCONHECIDO")
        if not motivo.strip():
            raise LedgerError("importação exige motivo")
        if estado == CONFIRMADO and not doc_id.strip():
            raise LedgerError("CONFIRMADO exige doc_id")

        def op(conn):
            if self._obter(conn, chave) is not None:
                raise LedgerError(f"{chave} já existe no ledger")
            conn.execute(
                "INSERT INTO operacoes (chave, estado, doc_id, tentativas, atualizado) VALUES (?, ?, ?, 0, ?)",
                (chave, estado, doc_id.strip(), time.time()),
            )
            self._evento(conn, chave, f"HISTORICO:{estado}", motivo)
        self._tx(op)

    def reconciliar(self, chave: str, estado: str, motivo: str, doc_id: str = "") -> None:
        """Ação humana, por leitura no SOMA. Motivo obrigatório."""
        if estado not in _DESTINOS_RECONCILIACAO:
            raise LedgerError(f"reconciliação só aceita {_DESTINOS_RECONCILIACAO}")
        if not motivo.strip():
            raise LedgerError("reconciliação exige motivo")
        if estado == CONFIRMADO and not doc_id.strip():
            raise LedgerError("CONFIRMADO exige doc_id")

        def op(conn):
            reg = self._obter(conn, chave)
            if reg is None or reg.estado not in _RECONCILIAVEIS:
                raise LedgerError(f"{chave} não está reconciliável (estado {reg.estado if reg else 'inexistente'})")
            conn.execute(
                "UPDATE operacoes SET estado=?, doc_id=?, atualizado=? WHERE chave=?",
                (estado, doc_id.strip(), time.time(), chave),
            )
            self._evento(conn, chave, f"RECONCILIADO:{estado}", motivo)
        self._tx(op)

    def listar_bloqueados(self) -> list[Registo]:
        return self._ler(lambda conn: [
            Registo(*r) for r in conn.execute(
                "SELECT chave, estado, doc_id, tentativas FROM operacoes WHERE estado != ? ORDER BY chave",
                (NAO_CRIADO,),
            ).fetchall()
        ])

    def verificar_integridade(self) -> str:
        return self._ler(lambda conn: conn.execute("PRAGMA quick_check").fetchone()[0])
