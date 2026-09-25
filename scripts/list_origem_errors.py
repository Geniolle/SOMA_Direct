from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from domain.models import norm_basic
from services.sheets_service import GoogleSheetsService


def classify_error(text: str) -> str:
    if text.startswith("Sequencial invalido em DESCRICAO SOMA") or text.startswith("Sequencial inválido em DESCRIÇÃO SOMA"):
        return "Sequencial inválido em DESCRIÇÃO SOMA"
    if text.startswith("DESCRICAO SOMA duplicada no mesmo dia") or text.startswith("DESCRIÇÃO SOMA duplicada no mesmo dia"):
        return "DESCRIÇÃO SOMA duplicada no mesmo dia"
    if text.startswith("DATA divergente na origem"):
        return "DATA divergente na origem"
    if text.startswith("DOC. SOMA divergente na origem"):
        return "DOC. SOMA divergente na origem"
    if text.startswith("Sequencial ausente em DESCRICAO SOMA") or text.startswith("Sequencial ausente em DESCRIÇÃO SOMA"):
        return "Sequencial ausente em DESCRIÇÃO SOMA"
    return text


def main() -> int:
    sheets = GoogleSheetsService(Settings.from_env())
    values = sheets._ws.get_all_values()
    if not values:
        print("Sheet CONTAORDEM vazia.")
        return 1

    indices = {norm_basic(header): index for index, header in enumerate(values[0])}
    origem_index = indices.get(norm_basic("ORIGEM"))
    if origem_index is None:
        print("Coluna ORIGEM nao encontrada.")
        return 1

    errors: Counter[str] = Counter()
    types: Counter[str] = Counter()
    validado = 0
    vazio = 0
    linhas_com_erro = 0
    erros_atomicos = 0

    for row in values[1:]:
        value = row[origem_index].strip() if origem_index < len(row) else ""
        if not value:
            vazio += 1
            continue
        if value == "Validado":
            validado += 1
            continue
        linhas_com_erro += 1
        if value.startswith("Erro: "):
            parts = [part.strip() for part in value[6:].split("; ") if part.strip()]
        else:
            parts = [value]
        for part in parts:
            errors[part] += 1
            types[classify_error(part)] += 1
            erros_atomicos += 1

    print(f"VALIDADO\t{validado}")
    print(f"VAZIO\t{vazio}")
    print(f"LINHAS_COM_ERRO\t{linhas_com_erro}")
    print(f"ERROS_ATOMICOS\t{erros_atomicos}")
    print()
    print("OCORRENCIAS\tTIPO_ERRO")
    for text, count in types.most_common():
        print(f"{count}\t{text}")
    print()
    print("OCORRENCIAS\tERRO")
    for text, count in errors.most_common():
        print(f"{count}\t{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
