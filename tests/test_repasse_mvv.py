from __future__ import annotations

from decimal import Decimal
import pytest

from services.repasse_mvv_service import (
    BaseReceitasMes,
    RepasseMvvService,
    format_decimal_pt,
    parse_decimal,
)


def test_parse_decimal():
    assert parse_decimal("5.090,02") == Decimal("5090.02")
    assert parse_decimal("5090.02") == Decimal("5090.02")
    assert parse_decimal("118,14 €") == Decimal("118.14")
    assert parse_decimal("") == Decimal("0.00")
    assert parse_decimal(None) == Decimal("0.00")


def test_calcular_repasses_oficial_agosto_2026():
    """Valida a regra oficial com os números exatos fornecidos pelo utilizador para 08/2026."""
    bases = BaseReceitasMes(
        ano=2026,
        mes=8,
        doacoes_dizimos_ofertas=Decimal("5090.02"),
        receitas_lanchonete=Decimal("307.00"),
        receitas_livraria=Decimal("118.14"),
        total_entrada=Decimal("5515.16"),
        origem="TESTE",
    )

    repasses = RepasseMvvService.calcular_repasses(bases)
    assert len(repasses) == 5

    mapa = {r.id_plano_contas: r for r in repasses}

    # 1. Dízimos 10%: (10% de 5090.02 = 509.00) + (3% de 307.00 = 9.21) = 518.21
    r_dizimos = mapa["369"]
    assert r_dizimos.valor == Decimal("518.21")
    assert r_dizimos.conta_santander_id == "5"
    assert r_dizimos.conta_santander_numero == "13002676-4"

    # 2. Oferta COVV 2%: 2% de 5090.02 = 101.80
    r_covv = mapa["370"]
    assert r_covv.valor == Decimal("101.80")
    assert r_covv.conta_santander_id == "6"

    # 3. Oferta Novas Obras 2%: 2% de 5090.02 = 101.80
    r_novas = mapa["372"]
    assert r_novas.valor == Decimal("101.80")
    assert r_novas.conta_santander_id == "8"

    # 4. Oferta Missões 1%: 1% de 5090.02 = 50.90
    r_missoes = mapa["371"]
    assert r_missoes.valor == Decimal("50.90")
    assert r_missoes.conta_santander_id == "7"

    # 5. Repasse Livraria 3%: 3% de 118.14 = 3.54
    r_livraria = mapa["373"]
    assert r_livraria.valor == Decimal("3.54")
    assert r_livraria.conta_santander_id == "5"

    # Soma total deve ser rigorosamente 776.25
    soma_total = sum((r.valor for r in repasses), Decimal("0.00"))
    assert soma_total == Decimal("776.25")


def test_dry_run_submission():
    from unittest.mock import MagicMock
    from config.settings import Settings

    mock_settings = Settings(
        site_user="user",
        site_password="pwd",
    )
    mock_http = MagicMock()
    service = RepasseMvvService(mock_settings, mock_http)

    bases = BaseReceitasMes(
        ano=2026,
        mes=8,
        doacoes_dizimos_ofertas=Decimal("5090.02"),
        receitas_lanchonete=Decimal("307.00"),
        receitas_livraria=Decimal("118.14"),
        total_entrada=Decimal("5515.16"),
        origem="TESTE",
    )
    repasses = service.calcular_repasses(bases)
    res = service.submeter_repasse(repasses[0], ano=2026, mes=8, dry_run=True)

    assert res.sucesso is True
    assert res.repasse_id is None
    assert "Dry-run" in res.mensagem
    mock_http.post.assert_not_called()
