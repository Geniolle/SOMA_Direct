"""Testes do planeamento (DRY_RUN) da reorganização de centros de custos — sem acesso ao SOMA."""
from domain.cost_center_plan import KEEP, MANUAL, PRESERVE, RECODE, RENAME, MappingEntry, TargetCenter
from services.cost_center_service import (
    CONFLICT,
    CREATE,
    READY,
    REVIEW,
    UNCHANGED,
    CostCenter,
    build_dry_run,
    find_duplicates,
    parse_label,
    parse_select_options,
)

PLAN = [
    TargetCenter("10.10.01", "DÍZIMOS"),
    TargetCenter("10.10.02", "OFERTAS GERAIS"),
    TargetCenter("10.10.03", "OFERTA ALÇADA"),
    TargetCenter("20.10.01", "ALUGUER DE INSTALAÇÕES"),
    TargetCenter("40.30.01", "REPASSE MVV"),
    TargetCenter("10.20.01", "VERBO LIVRARIA"),
]


def cc(id_, label, usage=None):
    return CostCenter(id=id_, label=label, usage=usage)


def by_target(ops, code):
    return [op for op in ops if op.target_code == code]


def test_parse_label_and_select_catalog():
    assert parse_label("20.10.01 - ALUGUER") == ("20.10.01", "ALUGUER")
    assert parse_label("PADRÃO") == ("", "PADRÃO")
    html = "<select><option value='0'>PADRÃO</option><option value='6226'>10.10.01 - DÍZIMOS E OFERTAS</option></select>"
    centers = parse_select_options(html)
    assert [(c.id, c.code) for c in centers] == [("0", ""), ("6226", "10.10.01")]


def test_duplicate_codes_are_detected():
    inv = [cc("1", "20.10.08 - MENSALIDADE CONTABILIDADE"), cc("2", "20.10.08 - PASTOR PRESIDENTE")]
    assert find_duplicates(inv)["codigos"] == {"20.10.08": ["1", "2"]}


def test_rename_keeps_internal_id_and_reports_history():
    inv = [cc("77", "20.10.01 - ALUGUER", usage=33)]
    ops = build_dry_run(inv, [MappingEntry("20.10.01 - ALUGUER", RENAME, "20.10.01")], PLAN)
    op = by_target(ops, "20.10.01")[0]
    assert (op.operation, op.center_id, op.status) == (RENAME, "77", READY)
    assert "33" in op.historical_impact


def test_identity_never_by_code_only():
    # Mesmo código, descrição diferente: não é o centro do mapeamento.
    inv = [cc("5", "20.10.01 - OUTRA COISA")]
    ops = build_dry_run(inv, [MappingEntry("20.10.01 - ALUGUER", RENAME, "20.10.01")], PLAN)
    mapped = [op for op in ops if op.current_label == "20.10.01 - ALUGUER"][0]
    assert mapped.status == REVIEW
    stray = [op for op in ops if op.center_id == "5"][0]
    assert stray.status == REVIEW  # fora do mapeamento: decisão humana


def test_ambiguous_description_is_conflict():
    inv = [cc("1", "20.10.01 - ALUGUER"), cc("2", "20.10.01 - ALUGUER")]
    ops = build_dry_run(inv, [MappingEntry("20.10.01 - ALUGUER", RENAME, "20.10.01")], PLAN)
    assert by_target(ops, "20.10.01")[0].status == CONFLICT


def test_manual_and_preserved_centers_are_never_ready():
    inv = [cc("0", "PADRÃO", usage=189), cc("9", "10.10.01 - DÍZIMOS E OFERTAS", usage=1873)]
    mapping = [MappingEntry("PADRÃO", PRESERVE, None), MappingEntry("10.10.01 - DÍZIMOS E OFERTAS", MANUAL, "10.10.01")]
    ops = build_dry_run(inv, mapping, PLAN)
    statuses = {op.center_id: op.status for op in ops if op.center_id}
    assert statuses == {"0": UNCHANGED, "9": REVIEW}


def test_recode_chain_is_ordered_by_real_dependencies():
    inv = [cc("1", "10.20.01 - REPASSE MVV"), cc("2", "10.10.04 - VERBO LIVRARIA")]
    mapping = [
        MappingEntry("10.10.04 - VERBO LIVRARIA", RECODE, "10.20.01"),  # listado primeiro de propósito
        MappingEntry("10.20.01 - REPASSE MVV", RECODE, "40.30.01"),
    ]
    ops = build_dry_run(inv, mapping, PLAN)
    order = {op.center_id: op.order for op in ops if op.center_id in ("1", "2")}
    assert order["1"] < order["2"]


def test_create_waits_for_code_to_be_freed():
    inv = [cc("3", "10.10.02 - OFERTA ALÇADA")]
    ops = build_dry_run(inv, [MappingEntry("10.10.02 - OFERTA ALÇADA", RECODE, "10.10.03")], PLAN)
    recode = [op for op in ops if op.center_id == "3"][0]
    create = [op for op in ops if op.operation == CREATE and op.target_code == "10.10.02"][0]
    assert create.status == READY and create.order > recode.order


def test_target_code_held_by_unmoved_center_is_conflict():
    inv = [cc("1", "10.10.02 - OFERTA ALÇADA"), cc("2", "10.10.03 - OFERTA ESPECIAL")]
    ops = build_dry_run(inv, [MappingEntry("10.10.02 - OFERTA ALÇADA", RECODE, "10.10.03")], PLAN)
    assert [op for op in ops if op.center_id == "1"][0].status == CONFLICT


def test_circular_dependency_is_conflict():
    plan = [TargetCenter("10.10.01", "A"), TargetCenter("10.10.02", "B")]
    inv = [cc("1", "10.10.01 - B"), cc("2", "10.10.02 - A")]
    mapping = [MappingEntry("10.10.01 - B", RECODE, "10.10.02"), MappingEntry("10.10.02 - A", RECODE, "10.10.01")]
    ops = build_dry_run(inv, mapping, plan)
    assert {op.status for op in ops if op.center_id} == {CONFLICT}


def test_rerun_after_partial_apply_is_idempotent():
    # O RENOMEAR já foi aplicado: o rótulo final existe e o antigo desapareceu.
    inv = [cc("77", "20.10.01 - ALUGUER DE INSTALAÇÕES")]
    ops = build_dry_run(inv, [MappingEntry("20.10.01 - ALUGUER", RENAME, "20.10.01")], PLAN)
    op = by_target(ops, "20.10.01")[0]
    assert op.status == UNCHANGED and op.center_id == "77"
    assert not [o for o in ops if o.operation == CREATE and o.target_code == "20.10.01"]


def test_existing_target_is_not_created_again():
    inv = [cc("8", "40.30.01 - REPASSE MVV")]
    ops = build_dry_run(inv, [], PLAN)
    assert not [o for o in ops if o.operation == CREATE and o.target_code == "40.30.01"]


def test_keep_entry_already_matching_is_unchanged():
    plan = [TargetCenter("20.10.02", "ENERGIA ELÉTRICA")]
    inv = [cc("4", "20.10.02 - ENERGIA ELÉTRICA")]
    ops = build_dry_run(inv, [MappingEntry("20.10.02 - ENERGIA ELÉTRICA", KEEP, "20.10.02")], plan)
    assert ops[0].status == UNCHANGED


def test_real_catalog_snapshot_plan():
    """Catálogo real (46 centros, 27/09/2026): duplicado 20.10.08 resolvido e nada eliminado."""
    from domain.cost_center_plan import MIGRATION_MAP

    labels = [e.current for e in MIGRATION_MAP]
    inv = [cc(str(i), label) for i, label in enumerate(labels)]
    ops = build_dry_run(inv)
    assert not [op for op in ops if op.status == CONFLICT]
    assert {op.operation for op in ops} <= {KEEP, RENAME, RECODE, MANUAL, PRESERVE, CREATE}
    ready_targets = [op.target_code for op in ops if op.status == READY]
    assert len(ready_targets) == len(set(ready_targets))  # nenhum código final duplicado
    assert "20.30.01" in ready_targets and "20.20.02" in ready_targets  # 20.10.08 desdobrado
