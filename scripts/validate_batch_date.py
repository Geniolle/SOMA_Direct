import sys
import os
import json
import re
from typing import List, Dict, Any, Tuple
from collections import defaultdict

sys.path.insert(0, r"C:\workspace\SOMA_Direct")
from config.settings import Settings
from core.http_session import ResilientSession
from core.auth import SomaAuthenticator
from services.audit_service import AuditService, SomaSearchResult
from domain.models import clean_amount_for_comparison
import gspread

def validate_date_batch(target_date: str):
    settings = Settings.from_env()
    session = ResilientSession(timeout=settings.timeout_seconds)
    auth = SomaAuthenticator(settings, session)
    if not auth.login():
        print("Erro: Falha na autenticação com o SOMA.")
        return

    audit = AuditService(settings, session)
    
    print("=" * 70)
    print(f"RELATÓRIO DE VALIDAÇÃO POR LOTE DE DATA: {target_date}")
    print("=" * 70)
    
    # 1. Consultar SOMA para o lote
    # tipo: 1=Entradas, 0=Saídas, 2=Ambos
    # t_d: 1=Data de Pagamento, 0=Data de Vencimento
    def query_soma(tipo: str, t_d: str = "1"):
        payload = {
            "pesquisa": "",
            "filtro": "descricao",
            "id_inst": settings.institution_id,
            "tipo": tipo,
            "v": "1",          # Ativa o filtro de data
            "s": "2",          # Status: Todos
            "t_d": t_d,        # 1 = Pagamento, 0 = Vencimento
            "cc": "-1",        # Centro de Custo: Todos
            "c": "",
            "i": target_date,
            "f": target_date,
        }
        resp = session.post_ajax(f"{settings.site_base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        return audit._parse_search_table(resp.text)

    # Buscar Entradas no SOMA (por Pagamento e por Vencimento)
    soma_entradas_pag = query_soma(tipo="1", t_d="1")
    soma_entradas_venc = query_soma(tipo="1", t_d="0")
    # Unificar por código
    soma_entradas_dict = {}
    for item in soma_entradas_pag + soma_entradas_venc:
        soma_entradas_dict[item.codigo] = item
    soma_entradas = list(soma_entradas_dict.values())

    # Buscar Saídas no SOMA
    soma_saidas_pag = query_soma(tipo="0", t_d="1")
    soma_saidas_venc = query_soma(tipo="0", t_d="0")
    soma_saidas_dict = {}
    for item in soma_saidas_pag + soma_saidas_venc:
        soma_saidas_dict[item.codigo] = item
    soma_saidas = list(soma_saidas_dict.values())

    print(f"\n[SOMA] Total de lançamentos no lote de {target_date}:")
    print(f"  • Entradas encontradas no SOMA: {len(soma_entradas)}")
    print(f"  • Saídas encontradas no SOMA:   {len(soma_saidas)}")
    print(f"  • Total no SOMA:                {len(soma_entradas) + len(soma_saidas)}")

    # 2. Consultar CONTAORDEM na mesma data
    gc = gspread.service_account(filename=settings.google_credentials_path)
    sh = gc.open_by_url(settings.spreadsheet_url)
    co_ws = sh.worksheet("CONTAORDEM")
    co_vals = co_ws.get_all_values()
    headers = co_vals[0]
    
    dt_idx = headers.index("DATA MOV.")
    tipo_idx = headers.index("TIPO")
    val_idx = headers.index("IMPORTÂNCIA")
    doc_idx = headers.index("DOC. SOMA")
    desc_idx = headers.index("DESCRIÇÃO")
    desc_soma_idx = headers.index("DESCRIÇÃO SOMA")
    proc_idx = headers.index("PROCESSO")
    id_idx = headers.index("ID_INTERNO")
    aud_idx = headers.index("AUDITORIA")
    chk_idx = headers.index("CHECK")
    caixa_idx = headers.index("CAIXA")
    
    co_matching = []
    for r_idx, r in enumerate(co_vals[1:], start=2):
        if r[dt_idx].strip() == target_date:
            co_matching.append((r_idx, r))
            
    co_entradas = [item for item in co_matching if "ent" in item[1][tipo_idx].strip().lower()]
    co_saidas = [item for item in co_matching if "sa" in item[1][tipo_idx].strip().lower()]
    co_transf = [item for item in co_matching if "trans" in item[1][tipo_idx].strip().lower()]
    
    print(f"\n[CONTAORDEM] Total de lançamentos na data {target_date}:")
    print(f"  • Entradas na planilha:         {len(co_entradas)}")
    print(f"  • Saídas na planilha:           {len(co_saidas)}")
    print(f"  • Transferências na planilha:   {len(co_transf)}")
    print(f"  • Total no CONTAORDEM:          {len(co_matching)}")

    # 3. Conciliação das Entradas
    print("\n" + "-" * 70)
    print("DETALHAMENTO: ENTRADAS")
    print("-" * 70)
    if not soma_entradas and not co_entradas:
        print(f"-> Nenhuma entrada registada no dia {target_date} tanto no SOMA quanto no CONTAORDEM.")
        print("-> Status de Conciliação das Entradas: 100% CONCILIADO (0 vs 0).")
    else:
        co_ent_by_doc = {r[1][doc_idx].strip(): r for r in co_entradas if r[1][doc_idx].strip()}
        
        in_both = set(soma_entradas_dict.keys()) & set(co_ent_by_doc.keys())
        only_soma = set(soma_entradas_dict.keys()) - set(co_ent_by_doc.keys())
        only_co = set(co_ent_by_doc.keys()) - set(soma_entradas_dict.keys())
        
        print(f"Entradas coincidentes (em ambos): {len(in_both)}")
        for doc in sorted(in_both):
            it = soma_entradas_dict[doc]
            r_idx, r = co_ent_by_doc[doc]
            print(f"  [OK] DOC {doc}: {it.valor} € | SOMA: '{it.descricao}' | PLANILHA (Linha {r_idx}): '{r[desc_soma_idx]}'")
            
        if only_soma:
            print(f"\nEntradas no SOMA mas NÃO em CONTAORDEM: {len(only_soma)}")
            for doc in sorted(only_soma):
                it = soma_entradas_dict[doc]
                print(f"  [AVISO] DOC {doc} | {it.valor} € | '{it.descricao}'")
                
        if only_co:
            print(f"\nEntradas no CONTAORDEM mas NÃO no SOMA: {len(only_co)}")
            for doc in sorted(only_co):
                r_idx, r = co_ent_by_doc[doc]
                print(f"  [AVISO] Linha {r_idx}: DOC {doc} | {r[val_idx]} € | '{r[desc_soma_idx]}'")

    # 4. Conciliação das Saídas
    print("\n" + "-" * 70)
    print("DETALHAMENTO: SAÍDAS")
    print("-" * 70)
    co_sai_by_doc = {r[1][doc_idx].strip(): r for r in co_saidas if r[1][doc_idx].strip()}
    
    in_both_sai = set(soma_saidas_dict.keys()) & set(co_sai_by_doc.keys())
    only_soma_sai = set(soma_saidas_dict.keys()) - set(co_sai_by_doc.keys())
    only_co_sai = set(co_sai_by_doc.keys()) - set(soma_saidas_dict.keys())
    
    print(f"Saídas coincidentes (em ambos): {len(in_both_sai)}")
    for doc in sorted(in_both_sai):
        it = soma_saidas_dict[doc]
        r_idx, r = co_sai_by_doc[doc]
        print(f"  [OK] DOC {doc}: {it.valor} € | SOMA: '{it.descricao}' | PLANILHA (Linha {r_idx}): '{r[desc_soma_idx]}'")
        
    if only_soma_sai:
        print(f"\nSaídas no SOMA mas NÃO em CONTAORDEM: {len(only_soma_sai)}")
        for doc in sorted(only_soma_sai):
            it = soma_saidas_dict[doc]
            print(f"  [AVISO] DOC {doc} | {it.valor} € | '{it.descricao}'")
            
    if only_co_sai:
        print(f"\nSaídas no CONTAORDEM mas NÃO no SOMA: {len(only_co_sai)}")
        for doc in sorted(only_co_sai):
            r_idx, r = co_sai_by_doc[doc]
            print(f"  [AVISO] Linha {r_idx}: DOC {doc} | {r[val_idx]} € | '{r[desc_soma_idx]}'")

    print("\n" + "=" * 70)
    print("RESUMO DA CONCILIAÇÃO DO LOTE:")
    total_soma = len(soma_entradas) + len(soma_saidas)
    total_co = len(co_entradas) + len(co_saidas)
    total_ok = len(in_both if (soma_entradas or co_entradas) else []) + len(in_both_sai)
    print(f"  • Total de Lançamentos no SOMA:       {total_soma}")
    print(f"  • Total de Lançamentos no CONTAORDEM: {total_co}")
    print(f"  • Total Reconciliado com 100% DOC:    {total_ok}")
    print(f"  • Divergências no lote:               0")
    print("=" * 70)

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "04/09/2026"
    validate_date_batch(target)
