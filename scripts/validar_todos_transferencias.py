#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Validação COMPLETA: Todos os 151 registos de transferência.
Busca no SOMA e valida cada um.
Gera relatório com status VALIDADO ou ERRO.
"""

from __future__ import annotations

import sys
from pathlib import Path
import re
from datetime import datetime, timedelta
from collections import defaultdict

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento, clean_amount_for_comparison, clean_caixa
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession


def extrair_transferencias_soma(html_response: str):
    """Extrai transferências da resposta HTML do SOMA."""
    transferencias = []

    rows = re.findall(r'<tr>(.*?)</tr>', html_response, re.IGNORECASE | re.DOTALL)

    for row_html in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.IGNORECASE | re.DOTALL)

        if len(cells) < 7:
            continue

        clean_cells = []
        for cell in cells:
            clean = re.sub(r'<[^>]+>', '', cell)
            clean = re.sub(r'&nbsp;', ' ', clean)
            clean = re.sub(r'&amp;', '&', clean)
            clean = clean.strip()
            clean_cells.append(clean)

        id_match = re.search(r'<input[^>]*value="(\d+)"[^>]*>', row_html)
        transfer_id = id_match.group(1) if id_match else ""

        if not transfer_id or len(clean_cells) < 7:
            continue

        caixa_origem = clean_cells[2].strip()
        valor_saida = clean_cells[3].strip()
        caixa_destino = clean_cells[4].strip()
        valor_entrada = clean_cells[5].strip()
        data = clean_cells[6].strip()

        if not data or not re.match(r'\d{2}/\d{2}/\d{4}', data):
            continue

        valor_saida = re.sub(r'[€\s]', '', valor_saida)
        valor_entrada = re.sub(r'[€\s]', '', valor_entrada)

        transferencias.append({
            "id": transfer_id,
            "caixa_origem": caixa_origem,
            "valor_saida": valor_saida,
            "caixa_destino": caixa_destino,
            "valor_entrada": valor_entrada,
            "data": data,
        })

    return transferencias


def transfer_key(data, valor, caixa_origem, caixa_destino) -> tuple:
    """Cria chave única para comparação."""
    return (
        data,
        clean_amount_for_comparison(valor),
        clean_caixa(caixa_origem),
        clean_caixa(caixa_destino),
    )


def main():
    """Valida todos os registos."""

    print("\n" + "=" * 160)
    print("✅ VALIDAÇÃO COMPLETA: Todos os registos de Transferência")
    print("=" * 160 + "\n")

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    # 1. Carregar registos
    print("📊 1. Carregando registos de CONTAORDEM...\n")
    all_rows = sheets.get_all_rows(only_entrada_saida=False)
    transferencias = [row for row in all_rows if row.tipo == TipoMovimento.TRANSFERENCIA]
    print(f"✅ {len(transferencias)} transferências carregadas\n")

    # 2. Autenticar
    print("🔐 2. Autenticando no SOMA...\n")
    http_session = ResilientSession(verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http_session)

    if not auth.login():
        print("❌ Falha na autenticação")
        return

    print("✅ Autenticado\n")

    # 3. Agrupar por datas para busca eficiente
    print("📅 3. Agrupando por datas para otimizar buscas...\n")

    datas_unicas = sorted(set(t.data_mov for t in transferencias))
    print(f"   {len(datas_unicas)} datas únicas encontradas\n")

    # Cache de buscas SOMA por período
    cache_soma = {}

    # 4. Validar cada registo
    print("🔄 4. Validando registos...\n")

    resultados = []
    stats = {"validado": 0, "erro": 0}
    erros_unicos = defaultdict(int)

    from ronda_completa import parse_date

    for idx, registo in enumerate(transferencias, 1):
        # Buscar em cache ou fazer requisição
        data_obj = parse_date(registo.data_mov)
        data_inicio = (data_obj - timedelta(days=7)).strftime("%d/%m/%Y")
        data_fim = (data_obj + timedelta(days=7)).strftime("%d/%m/%Y")

        cache_key = (data_inicio, data_fim)

        if cache_key not in cache_soma:
            # Buscar no SOMA
            url = f"{settings.site_base_url.rstrip('/')}/sys/post/buscarTransferenciasCaixas.php"
            response = http_session.session.post(
                url,
                data={"id_inst": "", "i": data_inicio, "f": data_fim},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=settings.timeout_seconds
            )
            cache_soma[cache_key] = extrair_transferencias_soma(response.text)

        soma_transfers = cache_soma[cache_key]

        # Procurar correspondência
        chave_registo = transfer_key(
            registo.data_mov,
            registo.importancia,
            registo.caixa_saida,
            registo.caixa
        )

        encontrado = None
        for soma_t in soma_transfers:
            chave_soma = transfer_key(
                soma_t['data'],
                soma_t['valor_saida'],
                soma_t['caixa_origem'],
                soma_t['caixa_destino']
            )

            if chave_soma == chave_registo:
                encontrado = soma_t
                break

        # Registar resultado
        if encontrado:
            resultado = {
                "id_interno": registo.id_interno,
                "data": registo.data_mov,
                "valor": registo.importancia,
                "status": "✅ VALIDADO",
                "soma_id": encontrado['id'],
                "erro": None,
            }
            stats["validado"] += 1
        else:
            # Procurar o erro mais próximo
            erro = "Não encontrado no SOMA"

            if soma_transfers:
                # Procurar qual campo não coincide
                for soma_t in soma_transfers:
                    data_ok = soma_t['data'] == registo.data_mov
                    valor_ok = clean_amount_for_comparison(soma_t['valor_saida']) == clean_amount_for_comparison(registo.importancia)
                    origem_ok = clean_caixa(soma_t['caixa_origem']) == clean_caixa(registo.caixa_saida)
                    destino_ok = clean_caixa(soma_t['caixa_destino']) == clean_caixa(registo.caixa)

                    # Se 3 dos 4 campos coincidem, é erro próximo
                    if sum([data_ok, valor_ok, origem_ok, destino_ok]) >= 3:
                        problemas = []
                        if not data_ok:
                            problemas.append(f"Data divergente ({soma_t['data']} vs {registo.data_mov})")
                        if not valor_ok:
                            problemas.append(f"Valor divergente ({soma_t['valor_saida']} vs {registo.importancia})")
                        if not origem_ok:
                            problemas.append(f"Origem divergente ({clean_caixa(soma_t['caixa_origem'])} vs {clean_caixa(registo.caixa_saida)})")
                        if not destino_ok:
                            problemas.append(f"Destino divergente ({clean_caixa(soma_t['caixa_destino'])} vs {clean_caixa(registo.caixa)})")

                        erro = " | ".join(problemas)
                        break

            resultado = {
                "id_interno": registo.id_interno,
                "data": registo.data_mov,
                "valor": registo.importancia,
                "status": "❌ ERRO",
                "soma_id": None,
                "erro": erro,
            }
            stats["erro"] += 1
            erros_unicos[erro] += 1

        resultados.append(resultado)

        # Mostrar progresso
        if idx <= 5 or idx == len(transferencias) or idx % 30 == 0:
            print(f"   [{idx:3d}/{len(transferencias)}] {resultado['status']} {resultado['id_interno']}")

    # 5. Relatório final
    print("\n" + "=" * 160)
    print("📊 RELATÓRIO FINAL\n")

    print(f"Total de registos:     {len(transferencias)}")
    print(f"✅ Validados:          {stats['validado']} ({stats['validado']/len(transferencias)*100:.1f}%)")
    print(f"❌ Com Erros:          {stats['erro']} ({stats['erro']/len(transferencias)*100:.1f}%)\n")

    if erros_unicos:
        print("Tipos de erro encontrados:\n")
        for erro, count in sorted(erros_unicos.items(), key=lambda x: x[1], reverse=True):
            print(f"  • {erro}")
            print(f"    Ocorrências: {count}\n")

    # 6. Listar registos com erro
    if stats["erro"] > 0:
        print("-" * 160)
        print(f"⚠️  REGISTOS COM ERRO ({stats['erro']}):\n")

        for idx, resultado in enumerate(resultados, 1):
            if resultado['status'] == "❌ ERRO":
                print(f"[{idx}] {resultado['id_interno']}")
                print(f"    Data:   {resultado['data']}")
                print(f"    Valor:  {resultado['valor']}")
                print(f"    Erro:   {resultado['erro']}\n")

    print("=" * 160)

    # 7. Sumário
    print("\n✅ VALIDAÇÃO CONCLUÍDA!\n")

    if stats["erro"] == 0:
        print("🎉 TODOS OS REGISTOS FORAM VALIDADOS COM SUCESSO!")
    else:
        print(f"⚠️  {stats['erro']} registos com divergências encontradas.")

    print("\n" + "=" * 160 + "\n")


if __name__ == "__main__":
    main()
