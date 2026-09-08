import sys
import os
import json
import time
import calendar
from datetime import datetime
from collections import defaultdict, Counter

sys.path.insert(0, r"C:\workspace\SOMA_Direct")
from config.settings import Settings
from core.http_session import ResilientSession
from core.auth import SomaAuthenticator
from services.audit_service import AuditService, SomaSearchResult
from domain.models import clean_amount_for_comparison, is_entrada_ou_saida
import gspread

def safe_call(func, *args, **kwargs):
    for i in range(5):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e) and i < 4:
                print(f"429 Quota limit hit, sleeping {(i+1)*10}s...")
                time.sleep((i+1)*10)
            else:
                raise

def get_month_range(year, month):
    _, last_day = calendar.monthrange(year, month)
    return f"01/{month:02d}/{year}", f"{last_day:02d}/{month:02d}/{year}"

def main():
    start_time = time.time()
    print("=" * 75)
    print("INICIANDO PROCESSAMENTO POR LOTE DE DATAS - CONTAORDEM vs SOMA")
    print("=" * 75)
    
    settings = Settings.from_env()
    session = ResilientSession(timeout=settings.timeout_seconds)
    auth = SomaAuthenticator(settings, session)
    if not auth.login():
        print("Erro crítico: Falha no login do SOMA.")
        return
        
    audit = AuditService(settings, session)
    
    # 1. Carregar CONTAORDEM
    print("\n[1/4] Lendo dados da planilha CONTAORDEM...")
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
    aud_idx = headers.index("AUDITORIA")
    chk_idx = headers.index("CHECK")
    proc_idx = headers.index("PROCESSO")
    id_idx = headers.index("ID_INTERNO")
    
    # Agrupar linhas por data
    rows_by_date = defaultdict(list)
    months_needed = set()
    
    for r_idx, r in enumerate(co_vals[1:], start=2):
        dt = r[dt_idx].strip()
        if not dt:
            continue
        tipo = r[tipo_idx].strip()
        if not is_entrada_ou_saida(tipo):
            continue
        try:
            d_obj = datetime.strptime(dt, "%d/%m/%Y")
            months_needed.add((d_obj.year, d_obj.month))
            rows_by_date[dt].append((r_idx, r))
        except ValueError:
            pass
            
    print(f"Total de linhas no CONTAORDEM: {len(co_vals)-1}")
    print(f"Total de lotes de datas: {len(rows_by_date)}")
    print(f"Total de meses a consultar no SOMA: {len(months_needed)}")
    
    # 2. Carregar movimentos do SOMA mês a mês
    print("\n[2/4] Carregando histórico completo do SOMA...")
    soma_by_date = defaultdict(list)
    soma_by_code = {}
    
    sorted_months = sorted(list(months_needed))
    for idx_m, (yr, mo) in enumerate(sorted_months, 1):
        d_start, d_end = get_month_range(yr, mo)
        for td in ("1", "0"): # Pagamento e Vencimento
            payload = {
                "pesquisa": "",
                "filtro": "descricao",
                "id_inst": settings.institution_id,
                "tipo": "2",
                "v": "1",
                "s": "2",
                "t_d": td,
                "cc": "-1",
                "c": "",
                "i": d_start,
                "f": d_end,
            }
            try:
                resp = session.post_ajax(f"{settings.site_base_url}sys/post/buscarEntradasSaidas.php", data=payload)
                items = audit._parse_search_table(resp.text)
                for it in items:
                    if it.codigo not in soma_by_code:
                        soma_by_code[it.codigo] = it
                        soma_by_date[it.data].append(it)
            except Exception as e:
                print(f"Aviso ao consultar SOMA para {yr}-{mo:02d} (t_d={td}): {e}")
                
        if idx_m % 6 == 0 or idx_m == len(sorted_months):
            print(f"  Progresso SOMA: {idx_m}/{len(sorted_months)} meses processados ({len(soma_by_code)} movimentos carregados).")
            
    print(f"Base SOMA carregada com sucesso: {len(soma_by_code)} lançamentos únicos mapeados.")
    
    # 3. Processar Lote por Lote e Atualizar AUDITORIA
    print("\n[3/4] Processando e atualizando a coluna AUDITORIA lote a lote...")
    
    # Ordenar datas cronologicamente decrescente
    sorted_dates = sorted(list(rows_by_date.keys()), key=lambda d: datetime.strptime(d, "%d/%m/%Y"), reverse=True)
    
    pending_updates = []
    total_confirmados = 0
    total_corrigidos = 0
    total_pendentes = 0
    total_divergentes = 0
    
    def flush_updates():
        if not pending_updates:
            return
        safe_call(co_ws.batch_update, pending_updates)
        pending_updates.clear()
        time.sleep(0.8) # Respeita cota de escrita
        
    for batch_idx, dt in enumerate(sorted_dates, 1):
        batch_rows = rows_by_date[dt]
        soma_items_on_date = soma_by_date.get(dt, [])
        soma_codes_on_date = {it.codigo: it for it in soma_items_on_date}
        
        used_soma_codes = set()
        
        for r_idx, r in batch_rows:
            current_doc = r[doc_idx].strip()
            current_aud = r[aud_idx].strip()
            tipo = r[tipo_idx].strip()
            val = r[val_idx].strip()
            clean_val = clean_amount_for_comparison(val)
            
            new_aud = current_aud
            new_doc = current_doc
            
            # Regra 1: Transferências Internas
            if tipo.lower() == "transferência" or current_doc.lower() == "transferido":
                new_aud = "Confirmado"
                total_confirmados += 1
                
            # Regra 2: Linha possui DOC numérico
            elif current_doc.isdigit():
                if current_doc in soma_codes_on_date:
                    soma_item = soma_codes_on_date[current_doc]
                    used_soma_codes.add(current_doc)
                    if clean_amount_for_comparison(soma_item.valor) == clean_val:
                        new_aud = "Confirmado"
                        total_confirmados += 1
                    else:
                        new_aud = f"Valor divergente: site={soma_item.valor} != sheet={val}"
                        total_divergentes += 1
                elif current_doc in soma_by_code:
                    soma_item = soma_by_code[current_doc]
                    used_soma_codes.add(current_doc)
                    if clean_amount_for_comparison(soma_item.valor) == clean_val:
                        new_aud = "Confirmado"
                        total_confirmados += 1
                    else:
                        new_aud = f"Valor divergente: site={soma_item.valor} != sheet={val}"
                        total_divergentes += 1
                else:
                    # DOC não encontrado no SOMA. Verifica se há movimento sem vínculo nessa data
                    available_matches = [
                        it for it in soma_items_on_date 
                        if it.codigo not in used_soma_codes and clean_amount_for_comparison(it.valor) == clean_val
                    ]
                    if len(available_matches) == 1:
                        new_doc = available_matches[0].codigo
                        used_soma_codes.add(new_doc)
                        new_aud = "Corrigido"
                        total_corrigidos += 1
                        pending_updates.append({
                            "range": f"E{r_idx}",
                            "values": [[new_doc]]
                        })
                    else:
                        new_aud = f"Código {current_doc} não existe no SOMA"
                        total_divergentes += 1
                        
            # Regra 3: Linha com DOC vazio (ex: novas vendas de 2025)
            elif not current_doc:
                available_matches = [
                    it for it in soma_items_on_date 
                    if it.codigo not in used_soma_codes and clean_amount_for_comparison(it.valor) == clean_val
                ]
                if len(available_matches) == 1:
                    new_doc = available_matches[0].codigo
                    used_soma_codes.add(new_doc)
                    new_aud = "Corrigido"
                    total_corrigidos += 1
                    pending_updates.append({
                        "range": f"E{r_idx}",
                        "values": [[new_doc]]
                    })
                else:
                    new_aud = "Pendente lançamento SOMA"
                    total_pendentes += 1
                    
            # Se o AUDITORIA mudou, agenda atualização
            if new_aud != current_aud:
                pending_updates.append({
                    "range": f"R{r_idx}",
                    "values": [[new_aud]]
                })
                
        # Atualiza a cada 50 linhas ou no término do lote
        if len(pending_updates) >= 50 or batch_idx == len(sorted_dates):
            flush_updates()
            
        if batch_idx % 100 == 0 or batch_idx == len(sorted_dates):
            print(f"  [Progresso] Lote {batch_idx}/{len(sorted_dates)} processado (Data: {dt}).")

    # Garante que qualquer atualização pendente seja enviada
    flush_updates()
    
    # 4. Verificação Final e Métricas
    print("\n[4/4] Verificando métricas finais do CONTAORDEM...")
    time.sleep(2)
    verify_vals = safe_call(co_ws.col_values, aud_idx + 1)[1:]
    final_counts = Counter(v.strip() for v in verify_vals)
    
    elapsed = time.time() - start_time
    print("\n" + "=" * 75)
    print(f"PROCESSAMENTO CONCLUÍDO EM {elapsed:.1f} SEGUNDOS")
    print("=" * 75)
    print(f"Total de Linhas no CONTAORDEM: {len(verify_vals)}")
    print("\nDISTRIBUIÇÃO DA COLUNA AUDITORIA:")
    for status, count in final_counts.most_common():
        pct = (count / len(verify_vals)) * 100
        print(f"  • {status:40s}: {count:5d} ({pct:6.2f}%)")
        
    print("=" * 75)

if __name__ == "__main__":
    main()
