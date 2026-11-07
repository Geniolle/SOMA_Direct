"""Auditoria e planeamento (DRY_RUN) da reorganização de centros de custos no SOMA.

Este módulo é deliberadamente só de leitura/planeamento:
- interpreta o catálogo de centros de custos devolvido pelo SOMA;
- deteta duplicados e ambiguidades;
- calcula, a partir de ``domain.cost_center_plan``, as operações necessárias,
  a ordem imposta pelas dependências reais de códigos e o estado de cada uma.

Nenhuma função aqui escreve no SOMA. A escrita (APPLY) só será implementada depois
de aprovada e de conhecido o endpoint real de gravação.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from html import unescape
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from domain.cost_center_plan import (
    KEEP,
    MANUAL,
    MIGRATION_MAP,
    PRESERVE,
    RECODE,
    RENAME,
    TARGET_BY_CODE,
    TARGET_PLAN,
    MappingEntry,
    TargetCenter,
)

CREATE = "CRIAR"

READY = "PRONTO"
CONFLICT = "CONFLITO"
REVIEW = "REVISÃO MANUAL"
UNCHANGED = "INALTERADO"

LABEL_RE = re.compile(r"^\s*(\d{2}\.\d{2}\.\d{2})\s*-\s*(.+?)\s*$")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.upper().split())


def parse_label(label: str) -> Tuple[str, str]:
    """'20.10.01 - ALUGUER' -> ('20.10.01', 'ALUGUER'); sem código -> ('', label)."""
    m = LABEL_RE.match(label or "")
    if m:
        return m.group(1), m.group(2)
    return "", (label or "").strip()


@dataclass
class CostCenter:
    id: str
    label: str
    active: Optional[bool] = None
    usage: Optional[int] = None        # nº de lançamentos associados (None = não verificado)
    usage_capped: bool = False         # True se a contagem atingiu o limite da pesquisa
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def code(self) -> str:
        return parse_label(self.label)[0]

    @property
    def description(self) -> str:
        return parse_label(self.label)[1]


def parse_select_options(html: str) -> List[CostCenter]:
    """Lê o <select> devolvido por sys/post/buscarCentrodeCustoSelect.php."""
    centers = []
    for value, text in re.findall(r"<option\b[^>]*value=[\"']([^\"']*)[\"'][^>]*>(.*?)</option>", html or "", re.S | re.I):
        label = " ".join(unescape(re.sub(r"<[^>]+>", " ", text)).split())
        if value.strip() == "" and not label:
            continue
        centers.append(CostCenter(id=value.strip(), label=label))
    return centers


def find_duplicates(inventory: Sequence[CostCenter]) -> Dict[str, Dict[str, List[str]]]:
    by_code: Dict[str, List[str]] = defaultdict(list)
    by_desc: Dict[str, List[str]] = defaultdict(list)
    by_id: Dict[str, List[str]] = defaultdict(list)
    for c in inventory:
        if c.code:
            by_code[c.code].append(c.id)
        by_desc[norm(c.description)].append(c.id)
        by_id[c.id].append(c.label)
    return {
        "codigos": {k: v for k, v in by_code.items() if len(v) > 1},
        "descricoes": {k: v for k, v in by_desc.items() if len(v) > 1},
        "ids": {k: v for k, v in by_id.items() if len(v) > 1},
    }


@dataclass
class PlannedOp:
    operation: str
    center_id: str
    current_label: str
    target_code: str
    target_description: str
    status: str
    reason: str = ""
    historical_impact: str = ""
    order: Optional[int] = None
    depends_on: List[str] = field(default_factory=list)

    @property
    def current_code(self) -> str:
        return parse_label(self.current_label)[0]

    @property
    def current_description(self) -> str:
        return parse_label(self.current_label)[1]

    @property
    def target_label(self) -> str:
        return f"{self.target_code} - {self.target_description}" if self.target_code else ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "ordem": self.order,
            "operacao": self.operation,
            "id_interno": self.center_id,
            "codigo_atual": self.current_code,
            "descricao_atual": self.current_description,
            "codigo_proposto": self.target_code,
            "descricao_proposta": self.target_description,
            "impacto_historico": self.historical_impact,
            "estado": self.status,
            "motivo": self.reason,
            "depende_de": self.depends_on,
        }


def _impact(center: Optional[CostCenter], operation: str) -> str:
    if center is None:
        return "Novo registo: sem histórico."
    if operation not in (RENAME, RECODE):
        return ""
    if center.usage is None:
        return "Uso histórico não verificado no SOMA."
    if center.usage == 0:
        return "Sem lançamentos associados."
    capped = "+" if center.usage_capped else ""
    return (f"{center.usage}{capped} lançamento(s) passam a apresentar o novo código/descrição "
            "(o ID interno não muda; relatórios históricos mudam de rótulo).")


def build_dry_run(
    inventory: Sequence[CostCenter],
    mapping: Iterable[MappingEntry] = MIGRATION_MAP,
    target_plan: Iterable[TargetCenter] = TARGET_PLAN,
) -> List[PlannedOp]:
    """Calcula as operações (sem escrever nada), com estado e ordem por dependências."""
    target_by_code = {t.code: t for t in target_plan} or TARGET_BY_CODE
    by_label: Dict[str, List[CostCenter]] = defaultdict(list)
    for c in inventory:
        by_label[norm(c.label)].append(c)
    target_labels = {norm(t.label) for t in target_by_code.values()}

    ops: List[PlannedOp] = []
    covered_ids = set()
    manual_targets = set()

    for entry in mapping:
        target = target_by_code.get(entry.target_code or "")
        t_code = target.code if target else ""
        t_desc = target.description if target else ""
        matches = by_label.get(norm(entry.current), [])
        for c in matches:
            covered_ids.add(c.id)

        if entry.operation == MANUAL and t_code:
            manual_targets.add(t_code)

        if not matches:
            already = by_label.get(norm(target.label), []) if target else []
            if already and entry.operation in (RENAME, RECODE, KEEP):
                ops.append(PlannedOp(entry.operation, already[0].id, already[0].label, t_code, t_desc,
                                     UNCHANGED, "Já aplicado (registo com o rótulo final existe)."))
            else:
                ops.append(PlannedOp(entry.operation, "", entry.current, t_code, t_desc, REVIEW,
                                     "Centro do mapeamento não encontrado no inventário real."))
            continue
        if len(matches) > 1:
            ids = ", ".join(c.id for c in matches)
            ops.append(PlannedOp(entry.operation, ids, entry.current, t_code, t_desc, CONFLICT,
                                 f"Descrição repetida em vários IDs ({ids}); identidade ambígua."))
            continue

        c = matches[0]
        if entry.operation == PRESERVE:
            ops.append(PlannedOp(PRESERVE, c.id, c.label, c.code, c.description, UNCHANGED, entry.note))
            continue
        if entry.operation == MANUAL:
            ops.append(PlannedOp(MANUAL, c.id, c.label, t_code, t_desc, REVIEW, entry.note, _impact(c, RECODE)))
            continue
        if norm(c.label) == norm(target.label):
            ops.append(PlannedOp(entry.operation, c.id, c.label, t_code, t_desc, UNCHANGED,
                                 "Já corresponde ao plano." if entry.operation != KEEP else entry.note))
            continue
        operation = RENAME if c.code == t_code else RECODE
        ops.append(PlannedOp(operation, c.id, c.label, t_code, t_desc, READY, entry.note, _impact(c, operation)))

    for c in inventory:
        if c.id not in covered_ids and norm(c.label) not in target_labels:
            ops.append(PlannedOp(MANUAL, c.id, c.label, "", "", REVIEW,
                                 "Centro existente fora do mapeamento: decidir manter, mapear ou inativar."))

    # Códigos ocupados no fim (registos que não se movem mantêm o código atual).
    moving = {op.center_id: op for op in ops if op.status == READY and op.operation in (RENAME, RECODE)}
    final_holder: Dict[str, List[str]] = defaultdict(list)
    for c in inventory:
        code = moving[c.id].target_code if c.id in moving else c.code
        if code:
            final_holder[code].append(c.id)
    for op in ops:
        if op.status == READY and len(final_holder[op.target_code]) > 1:
            others = [i for i in final_holder[op.target_code] if i != op.center_id]
            op.status = CONFLICT
            op.reason = (op.reason + " " if op.reason else "") + \
                f"Código {op.target_code} continuaria ocupado pelo(s) ID(s) {', '.join(others)}."

    # Criações: códigos do plano sem registo no fim.
    final_codes = {code for code, ids in final_holder.items() if ids}
    for target in target_by_code.values():
        if by_label.get(norm(target.label)) or target.code in final_codes:
            continue  # já existe ou ficará ocupado por um registo existente
        if target.code in manual_targets:
            ops.append(PlannedOp(CREATE, "", "", target.code, target.description, REVIEW,
                                 "Depende de uma decisão manual sobre o centro atual relacionado."))
        else:
            ops.append(PlannedOp(CREATE, "", "", target.code, target.description, READY, "", _impact(None, CREATE)))

    _order(ops, inventory)
    return ops


def _order(ops: List[PlannedOp], inventory: Sequence[CostCenter]) -> None:
    """Ordena por dependências: quem liberta um código vem antes de quem o ocupa."""
    holder_now: Dict[str, List[str]] = defaultdict(list)
    for c in inventory:
        if c.code:
            holder_now[c.code].append(c.id)
    key = lambda op: f"{op.operation}:{op.center_id or op.target_code}"
    actionable = [op for op in ops if op.status == READY]
    by_center = {op.center_id: op for op in actionable if op.center_id}
    deps: Dict[str, List[str]] = {}
    for op in actionable:
        blockers = []
        for holder in holder_now.get(op.target_code, []):
            if holder == op.center_id:
                continue
            if holder in by_center:
                blockers.append(key(by_center[holder]))
        op.depends_on = blockers
        deps[key(op)] = blockers

    done: List[str] = []
    remaining = {key(op): op for op in actionable}
    order = 1
    while remaining:
        ready_now = sorted(
            (k for k, op in remaining.items() if all(d in done for d in deps[k])),
            key=lambda k: (remaining[k].operation == CREATE, remaining[k].target_code),
        )
        if not ready_now:
            for op in remaining.values():
                op.status = CONFLICT
                op.reason = (op.reason + " " if op.reason else "") + "Dependência circular de códigos."
            break
        for k in ready_now:
            remaining[k].order = order
            order += 1
            done.append(k)
            del remaining[k]


def summarize(ops: Sequence[PlannedOp]) -> Dict[str, Dict[str, int]]:
    by_status: Dict[str, int] = defaultdict(int)
    by_op: Dict[str, int] = defaultdict(int)
    for op in ops:
        by_status[op.status] += 1
        by_op[op.operation] += 1
    return {"por_estado": dict(by_status), "por_operacao": dict(by_op)}
