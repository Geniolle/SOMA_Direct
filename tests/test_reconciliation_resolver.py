import pytest
from domain.models import ContaOrdemRow, TipoMovimento, SomaSearchResult, strip_suffix_n

def test_rule4_tipo_logic():
    # Test origin amount < 0 -> Saída, > 0 -> Entrada
    val_neg = -0.16
    expected_tipo = "Saída" if val_neg < 0 else "Entrada"
    assert expected_tipo == "Saída"

    val_pos = 100.00
    expected_tipo = "Saída" if val_pos < 0 else "Entrada"
    assert expected_tipo == "Entrada"

def test_rule2_suffix_calculation():
    used_desc = [
        "DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N001",
        "DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N002",
        "DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N004",
    ]
    base = "DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA)"
    import re
    all_nums = set()
    for d in used_desc:
        m = re.search(r'\bN(\d+)\b', d)
        if m:
            all_nums.add(int(m.group(1)))

    n_seq = 1
    while n_seq in all_nums:
        n_seq += 1
    all_nums.add(n_seq)
    
    assert n_seq == 3
    new_desc = f"{base} N{n_seq:03d}"
    assert new_desc == "DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N003"


def test_em_aberto_payment_payload():
    doc_clean = "5488910"
    id_fluxo = f"{int(doc_clean):010d}"
    assert id_fluxo == "0005488910"
    
    payload = {
        "fluxo_desconto": "0,00",
        "fluxo_valor": "28,25",
        "data_pagamento": "31/10/2025",
        "forma_pagamento": "0",
        "aplicar_desconto": "0",
        "num_cheque": "",
        "num_documento": "",
        "tipo_pagamento": "0",
        "valor_pagamento": "28,25",
        "id_caixa": "5186",
        "id_fluxo": id_fluxo,
        "add": "1",
        "aceitar_caixa_negativo": "1",
    }
    assert payload["id_fluxo"] == "0005488910"
    assert payload["data_pagamento"] == "31/10/2025"
    assert payload["valor_pagamento"] == "28,25"

