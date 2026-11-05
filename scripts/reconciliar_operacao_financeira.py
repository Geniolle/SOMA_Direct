"""Gestão manual do ledger de operações financeiras.

Inicializar (uma vez, antes do primeiro arranque; cria 0600 fora da árvore do código):
  python scripts/reconciliar_operacao_financeira.py --inicializar

Verificar integridade e listar bloqueios:
  python scripts/reconciliar_operacao_financeira.py --verificar
  python scripts/reconciliar_operacao_financeira.py --listar

Reconciliar após verificar no portal SOMA, por leitura:
  python scripts/reconciliar_operacao_financeira.py --chave "T_EXTRATO|EXT0000003908" --estado CONFIRMADO --doc 5653763 --motivo "verificado no SOMA"
  python scripts/reconciliar_operacao_financeira.py --chave "T_EXTRATO|EXT0000003908" --estado NAO_CRIADO --motivo "pesquisa no SOMA sem resultados"

Só altera o ledger. Não acede ao SOMA nem à Google Sheets, e não limpa DOC. SOMA na sheet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.operation_ledger import (  # noqa: E402
    CONFIRMADO,
    NAO_CRIADO,
    LedgerError,
    OperationLedger,
    resolve_ledger_path,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", default=None, help="caminho do ledger (padrão: SOMA_LEDGER_PATH ou diretório de dados)")
    parser.add_argument("--inicializar", action="store_true", help="cria o ledger se não existir")
    parser.add_argument("--verificar", action="store_true", help="verifica integridade do ledger")
    parser.add_argument("--listar", action="store_true", help="lista operações bloqueadas")
    parser.add_argument("--importar", action="store_true", help="regista histórico inexistente no ledger (CONFIRMADO/RESULTADO_DESCONHECIDO)")
    parser.add_argument("--chave", help="PROCESSO|ID_INTERNO")
    parser.add_argument("--estado", choices=[CONFIRMADO, NAO_CRIADO, "RESULTADO_DESCONHECIDO"], help="resultado verificado")
    parser.add_argument("--doc", default="", help="DOC. SOMA (obrigatório para CONFIRMADO)")
    parser.add_argument("--motivo", default="", help="evidência da verificação (obrigatório)")
    args = parser.parse_args(argv)

    path = Path(args.ledger) if args.ledger else resolve_ledger_path()
    try:
        if args.inicializar:
            if path.exists():
                print(f"ledger já existe: {path}")
                return 0
            OperationLedger.inicializar(path).close()
            print(f"ledger criado: {path}")
            return 0

        ledger = OperationLedger(path)  # ausente, corrompido ou com permissões inseguras -> LedgerError
        try:
            if args.verificar:
                print(f"integridade: {ledger.verificar_integridade()}")
                return 0
            if args.listar:
                for reg in ledger.listar_bloqueados():
                    print(f"{reg.chave}\t{reg.estado}\tDOC={reg.doc_id or '-'}\ttentativas={reg.tentativas}")
                return 0
            if not args.chave or not args.estado:
                parser.error("--chave e --estado são obrigatórios (ou use --verificar / --listar / --inicializar)")
            if args.importar:
                ledger.importar_historico(args.chave, args.estado, args.motivo, doc_id=args.doc)
                print(f"{args.chave}: importado como {args.estado}")
                return 0
            ledger.reconciliar(args.chave, args.estado, args.motivo, doc_id=args.doc)
            print(f"{args.chave}: reconciliado como {args.estado}")
            return 0
        finally:
            ledger.close()
    except LedgerError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
