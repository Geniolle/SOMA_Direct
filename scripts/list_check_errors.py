from config.settings import Settings
from services.sheets_service import GoogleSheetsService

settings = Settings.from_env()
sheets = GoogleSheetsService(settings)
ws = sheets._sh.worksheet(settings.sheet_contaordem)
values = ws.get_all_values()
headers = values[0]

check_idx = headers.index('CHECK')
doc_idx = headers.index('DOC. SOMA')
dados_idx = headers.index('DADOS DOC')
caixa_idx = headers.index('CAIXA')
forma_idx = headers.index('FORMA DE PAGAMENTO')
dt_idx = headers.index('DATA MOV.')
val_idx = headers.index('IMPORTÂNCIA')
proc_idx = headers.index('PROCESSO')
id_idx = headers.index('ID_INTERNO')
desc_idx = headers.index('DESCRIÇÃO SOMA')

errors = []
for i, r in enumerate(values[1:], start=2):
    c = r[check_idx].strip() if len(r) > check_idx else ''
    if c.startswith('Erro'):
        errors.append({
            'linha': i,
            'doc': r[doc_idx].strip() if len(r) > doc_idx else '',
            'data': r[dt_idx].strip() if len(r) > dt_idx else '',
            'valor': r[val_idx].strip() if len(r) > val_idx else '',
            'processo': r[proc_idx].strip() if len(r) > proc_idx else '',
            'id_interno': r[id_idx].strip() if len(r) > id_idx else '',
            'descricao': r[desc_idx].strip() if len(r) > desc_idx else '',
            'caixa_co': r[caixa_idx].strip() if len(r) > caixa_idx else '',
            'forma_co': r[forma_idx].strip() if len(r) > forma_idx else '',
            'dados_doc': r[dados_idx].strip() if len(r) > dados_idx else '',
            'status': c,
        })

print(f"Total de erros encontrados: {len(errors)}")
print("=" * 100)
for e in errors:
    print(f"Linha {e['linha']:4d} | DOC: {e['doc']:7s} | {e['data']:10s} | {e['valor']:7s} € | {e['processo']:15s} | {e['id_interno']:14s}")
    print(f"   Descrição:   {e['descricao']}")
    print(f"   CONTAORDEM:  Caixa='{e['caixa_co']}', Forma='{e['forma_co']}'")
    print(f"   DADOS DOC:   {e['dados_doc']}")
    print(f"   DIAGNOSTICO: {e['status']}")
    print("-" * 100)
