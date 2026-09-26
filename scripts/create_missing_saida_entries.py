from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from domain.models import clean_amount_for_comparison, extract_suffix_n, norm_basic
from services.sheets_service import GoogleSheetsService

# Meses em português
MONTHS = (
    "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa linhas de SAÍDAS para CONTAORDEM (critério: coluna FINANCE vazia)"
    )
    parser.add_argument("--apply", action="store_true", help="Grava efetivamente as linhas em CONTAORDEM")
    args = parser.parse_args()

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    # Ler SAÍDAS
    print("Lendo folha SAÍDAS...")
    saidas_values = sheets.get_origin_worksheet("SAÍDAS").get_all_values()
    if not saidas_values or len(saidas_values) < 2:
        print("❌ Folha SAÍDAS vazia ou sem dados.")
        return

    # Mapear colunas de SAÍDAS
    saidas_headers = saidas_values[0]
    saidas_indices = {norm_basic(header): idx for idx, header in enumerate(saidas_headers)}

    # Verificar colunas obrigatórias
    required_saidas = ["ID_INTERNO", "DATA", "DESCRIÇÃO DA COMPRA", "VALOR DA COMPRA", "FINANCE"]
    missing = [col for col in required_saidas if norm_basic(col) not in saidas_indices]
    if missing:
        print(f"[ERRO] Colunas ausentes em SAÍDAS: {missing}")
        return

    # Ler CONTAORDEM
    print("Lendo folha CONTAORDEM...")
    co_values = sheets._ws.get_all_values()
    co_headers = co_values[0]
    co_indices = {norm_basic(header): idx for idx, header in enumerate(co_headers)}

    # Verificar colunas obrigatórias em CONTAORDEM
    required_co = ["ID_INTERNO", "DATA MOV.", "DESCRIÇÃO", "TIPO", "PROCESSO", "IMPORTÂNCIA"]
    missing_co = [col for col in required_co if norm_basic(col) not in co_indices]
    if missing_co:
        print(f"[ERRO] Colunas ausentes em CONTAORDEM: {missing_co}")
        return

    def saida_value(row, field):
        """Acessa valor de célula em SAÍDAS com tolerância a variações de nome."""
        idx = saidas_indices.get(norm_basic(field))
        if idx is None:
            return ""
        return row[idx].strip() if idx < len(row) else ""

    def co_value(row, field):
        """Acessa valor de célula em CONTAORDEM com tolerância a variações de nome."""
        idx = co_indices.get(norm_basic(field))
        if idx is None:
            return ""
        return row[idx].strip() if idx < len(row) else ""

    # Coletar IDs já em CONTAORDEM
    existing_ids = {
        co_value(row, "ID_INTERNO")
        for row in co_values[1:]
        if co_value(row, "ID_INTERNO")
    }

    # Agrupar SAÍDAS por data para calcular sequencial Nxxx
    saidas_by_date = defaultdict(list)
    rows_to_process = []

    for row_idx, saida_row in enumerate(saidas_values[1:], start=2):
        id_interno = saida_value(saida_row, "ID_INTERNO")
        finance_field = saida_value(saida_row, "FINANCE")

        # Critério: FINANCE vazio E ID_INTERNO ainda não em CONTAORDEM
        if not finance_field.strip() and id_interno and id_interno not in existing_ids:
            data_mov = saida_value(saida_row, "DATA")
            saidas_by_date[data_mov].append({
                "saida_row": saida_row,
                "id_interno": id_interno,
                "data_mov": data_mov,
                "row_number": row_idx,
            })
            rows_to_process.append(id_interno)

    print(f"\n[OK] Encontradas {len(rows_to_process)} linha(s) em SAÍDAS com FINANCE vazio e nao em CONTAORDEM")
    if not rows_to_process:
        print("[INFO] Nenhuma linha para importar.")
        return

    # Construir sequenciais por data
    rows_to_append = []
    seq_by_date = defaultdict(int)

    # Coletar sequenciais existentes em CONTAORDEM por data
    for co_row in co_values[1:]:
        data_mov = co_value(co_row, "DATA MOV.")
        desc_soma = co_value(co_row, "DESCRIÇÃO SOMA")
        seq = extract_suffix_n(desc_soma)
        if seq and data_mov:
            seq_by_date[data_mov] = max(seq_by_date.get(data_mov, 0), seq)

    # Processar cada SAÍDA
    for data_mov in sorted(saidas_by_date.keys()):
        items = saidas_by_date[data_mov]
        next_seq = seq_by_date.get(data_mov, 0) + 1

        for item in items:
            saida_row = item["saida_row"]
            id_interno = item["id_interno"]
            data_mov = item["data_mov"]

            # Extrair dados de SAÍDAS
            descricao = saida_value(saida_row, "DESCRIÇÃO DA COMPRA")
            valor = saida_value(saida_row, "VALOR DA COMPRA")
            tipo = saida_value(saida_row, "TIPO")

            # Montar linha para CONTAORDEM
            co_row = [""] * len(co_headers)

            # Mapear campos
            mapped = {
                "DATA MOV.": data_mov,
                "DESCRIÇÃO": descricao,
                "IMPORTÂNCIA": clean_amount_for_comparison(valor) if valor else "0,00",
                "TIPO": "Saída" if not tipo or norm_basic(tipo) == "saida" else tipo,
                "PROCESSO": "SAÍDAS",
                "ID_INTERNO": id_interno,
                "DESCRIÇÃO SOMA": f"{descricao} N{next_seq:03d}",
            }

            # Tenta adicionar período se a data for válida
            try:
                parsed_date = datetime.strptime(data_mov, "%d/%m/%Y")
                mapped["PERÍODO"] = MONTHS[parsed_date.month - 1]
            except (ValueError, IndexError):
                pass

            # Preencher linha
            for field, field_value in mapped.items():
                field_idx = co_indices.get(norm_basic(field))
                if field_idx is not None:
                    co_row[field_idx] = field_value

            rows_to_append.append(co_row)
            print(
                f"  Preparada: {id_interno} | {data_mov} | {descricao[:40]:40s} | "
                f"N{next_seq:03d} | {clean_amount_for_comparison(valor) if valor else '0,00'}"
            )
            next_seq += 1
            seq_by_date[data_mov] = next_seq - 1

    print(f"\n[INFO] Total preparado: {len(rows_to_append)} linha(s)")

    if args.apply and rows_to_append:
        try:
            sheets._ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
            print(f"[OK] Total criado em CONTAORDEM: {len(rows_to_append)} linha(s)")
        except Exception as e:
            print(f"[ERRO] Erro ao gravar em CONTAORDEM: {e}")
            sys.exit(1)
    else:
        print("[SIMULACAO] Nenhuma linha criada. Use --apply para confirmar.")


if __name__ == "__main__":
    main()
