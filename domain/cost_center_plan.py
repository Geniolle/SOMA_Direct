"""Plano-alvo de centros de custos (BRAGA - PORTUGAL) e mapeamento a partir do catálogo atual.

Só dados: nenhuma operação no SOMA é feita a partir deste módulo. O mapeamento é
indexado pela descrição ATUAL exata (como o SOMA a mostra no catálogo); o planeador
associa-a ao ID interno do inventário real antes de propor qualquer operação.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class TargetCenter:
    code: str
    description: str

    @property
    def label(self) -> str:
        return f"{self.code} - {self.description}"


@dataclass(frozen=True)
class GroupHeader:
    code: str
    description: str


GROUPS: List[GroupHeader] = [
    GroupHeader("10", "ENTRADAS E RECEITAS"),
    GroupHeader("10.10", "CONTRIBUIÇÕES DA IGREJA"),
    GroupHeader("10.20", "ATIVIDADES COMERCIAIS"),
    GroupHeader("10.30", "OUTRAS RECEITAS"),
    GroupHeader("20", "DESPESAS OPERACIONAIS"),
    GroupHeader("20.10", "INSTALAÇÕES E SERVIÇOS"),
    GroupHeader("20.20", "SERVIÇOS ADMINISTRATIVOS"),
    GroupHeader("20.30", "PASTOREIO E PESSOAL"),
    GroupHeader("20.40", "ENCARGOS BANCÁRIOS"),
    GroupHeader("20.50", "IMPOSTOS E OBRIGAÇÕES"),
    GroupHeader("30", "INFRAESTRUTURAS E INVESTIMENTOS"),
    GroupHeader("30.10", "MANUTENÇÃO"),
    GroupHeader("30.20", "MOBILIÁRIO E EQUIPAMENTOS"),
    GroupHeader("30.30", "MELHORIAS E TECNOLOGIA"),
    GroupHeader("40", "MINISTÉRIO E ATIVIDADES"),
    GroupHeader("40.10", "EVENTOS E COMUNHÃO"),
    GroupHeader("40.20", "MINISTROS E CONVIDADOS"),
    GroupHeader("40.30", "REPASSES MINISTERIAIS"),
    GroupHeader("50", "DEPARTAMENTOS"),
    GroupHeader("50.10", "MINISTÉRIOS DE APOIO"),
    GroupHeader("50.20", "ENSINO E FORMAÇÃO"),
    GroupHeader("50.30", "LOUVOR E ADORAÇÃO"),
    GroupHeader("50.40", "JUVENTUDE E FAMÍLIA"),
    GroupHeader("50.50", "MISSÕES"),
    GroupHeader("60", "MOVIMENTOS FINANCEIROS"),
    GroupHeader("60.10", "EMPRÉSTIMOS OBTIDOS"),
    GroupHeader("60.20", "TRANSFERÊNCIAS INTERNAS"),
]

TARGET_PLAN: List[TargetCenter] = [TargetCenter(c, d) for c, d in [
    ("10.10.01", "DÍZIMOS"),
    ("10.10.02", "OFERTAS GERAIS"),
    ("10.10.03", "OFERTA ALÇADA"),
    ("10.10.04", "OFERTAS ESPECIAIS"),
    ("10.20.01", "VERBO LIVRARIA"),
    ("10.20.02", "VERBO CAFÉ"),
    ("10.30.01", "DOAÇÕES E CONTRIBUIÇÕES DIVERSAS"),
    ("10.30.02", "RECEITAS DE EVENTOS"),
    ("20.10.01", "ALUGUER DE INSTALAÇÕES"),
    ("20.10.02", "ENERGIA ELÉTRICA"),
    ("20.10.03", "ÁGUA E SANEAMENTO"),
    ("20.10.04", "GÁS"),
    ("20.10.05", "INTERNET E COMUNICAÇÕES"),
    ("20.10.06", "SEGURANÇA E ALARMES"),
    ("20.20.01", "MENSALIDADES DE SOFTWARE"),
    ("20.20.02", "SERVIÇOS DE CONTABILIDADE"),
    ("20.20.03", "MATERIAL DE EXPEDIENTE"),
    ("20.20.04", "MATERIAL DE LIMPEZA"),
    ("20.20.05", "SEGUROS DE INSTALAÇÕES E TERCEIROS"),
    ("20.30.01", "PASTOR PRESIDENTE"),
    ("20.30.02", "PASTOR AUXILIAR"),
    ("20.30.03", "OUTROS ENCARGOS COM PESSOAL"),
    ("20.40.01", "COMISSÕES BANCÁRIAS"),
    ("20.40.02", "COMISSÕES TPA"),
    ("20.40.03", "IMPOSTO DO SELO BANCÁRIO"),
    ("20.50.01", "IMPOSTOS E TAXAS GOVERNAMENTAIS"),
    ("30.10.01", "MANUTENÇÃO PREDIAL"),
    ("30.10.02", "MÃO DE OBRA ESPECIALIZADA"),
    ("30.10.03", "REPARAÇÃO DE EQUIPAMENTOS"),
    ("30.20.01", "MOBILIÁRIO"),
    ("30.20.02", "EQUIPAMENTO MUSICAL"),
    ("30.20.03", "EQUIPAMENTOS ELETRÓNICOS"),
    ("30.20.04", "ALUGUER DE MÁQUINAS E EQUIPAMENTOS"),
    ("30.30.01", "DECORAÇÃO"),
    ("30.30.02", "LICENÇAS DE SOFTWARE"),
    ("30.30.03", "MELHORIAS NAS INSTALAÇÕES"),
    ("40.10.01", "DESPESAS COM EVENTOS"),
    ("40.10.02", "CONVÍVIOS"),
    ("40.20.01", "DESPESAS COM MINISTROS"),
    ("40.20.02", "TRANSPORTE E ALOJAMENTO DE MINISTROS"),
    ("40.30.01", "REPASSE MVV"),
    ("40.30.02", "REPASSE DIA DE MISSÕES"),
    ("50.10.01", "AÇÃO SOCIAL"),
    ("50.10.02", "DIACONATO"),
    ("50.20.01", "DEPARTAMENTO INFANTIL"),
    ("50.30.01", "GRUPO DE MÚSICA"),
    ("50.40.01", "JOVENS OMEGA"),
    ("50.40.02", "MULHERES COM PROPÓSITO"),
    ("50.40.03", "SACERDOTES DA FAMÍLIA"),
    ("50.50.01", "MISSÕES VERBO DA VIDA"),
    ("60.10.01", "ENTRADA DE EMPRÉSTIMO"),
    ("60.10.02", "AMORTIZAÇÃO DE EMPRÉSTIMO"),
    ("60.10.03", "JUROS DE EMPRÉSTIMO"),
    ("60.20.01", "TRANSFERÊNCIAS ENTRE CONTAS BANCÁRIAS"),
    ("60.20.02", "TRANSFERÊNCIAS ENTRE CAIXA E BANCO"),
]]

TARGET_BY_CODE: Dict[str, TargetCenter] = {t.code: t for t in TARGET_PLAN}

# Operações
KEEP = "MANTER"
RENAME = "RENOMEAR"          # mesmo código, nova descrição
RECODE = "RECODIFICAR"       # novo código (e possivelmente nova descrição)
MANUAL = "REVISAO_MANUAL"    # equivalência semântica incerta: decisão humana
PRESERVE = "PRESERVAR"       # centro de sistema / fora do plano: não tocar


@dataclass(frozen=True)
class MappingEntry:
    current: str                 # descrição atual exata no catálogo SOMA
    operation: str
    target_code: Optional[str]   # None para PRESERVAR
    note: str = ""


# Construído a partir do catálogo real (CCVV / buscarCentrodeCustoSelect, 46 centros)
# e do uso histórico na CONTAORDEM. Rever antes de qualquer APPLY.
MIGRATION_MAP: List[MappingEntry] = [
    MappingEntry("PADRÃO", PRESERVE, None, "Centro de sistema (ID 0) usado por omissão e pelas Transferências."),
    MappingEntry("10.10.01 - DÍZIMOS E OFERTAS", PRESERVE, None,
                 "Decisão 01/11/2026 (opção B): manter o nome atual neste ID para preservar o histórico "
                 "(inclui dízimos e ofertas). O novo '10.10.01 - DÍZIMOS' fica por decidir (código ocupado)."),
    MappingEntry("10.10.02 - OFERTA ALÇADA", RECODE, "10.10.03"),
    MappingEntry("10.10.03 - OFERTA ESPECIAL", RECODE, "10.10.04"),
    MappingEntry("10.10.04 - VERBO LIVRARIA", RECODE, "10.20.01"),
    MappingEntry("10.10.05 - VERBO CAFE", RECODE, "10.20.02"),
    MappingEntry("10.20.01 - REPASSE MVV", RECODE, "40.30.01"),
    MappingEntry("10.20.02 - REPASSE DIA DE MISSÕES", RECODE, "40.30.02"),
    MappingEntry("10.30.01 - COMISSÕES", RECODE, "20.40.01"),
    MappingEntry("10.30.02 - COMISSÃO TPA", RECODE, "20.40.02"),
    MappingEntry("10.30.03 - IMPOSTO DE SELO", RECODE, "20.40.03"),
    MappingEntry("10.40.01 - ENTRADA DE EMPRÉSTIMO", RECODE, "60.10.01"),
    MappingEntry("10.40.02 - SAÍDA DE EMPRÉSTIMO", RECODE, "60.10.02",
                 "Decisão 01/11/2026: equivalente a AMORTIZAÇÃO DE EMPRÉSTIMO."),
    MappingEntry("20.10.01 - ALUGUER", RENAME, "20.10.01"),
    MappingEntry("20.10.02 - ENERGIA ELÉTRICA", KEEP, "20.10.02"),
    MappingEntry("20.10.03 - ÁGUAS E SANEAMENTO", RENAME, "20.10.03"),
    MappingEntry("20.10.04 - GÁS", KEEP, "20.10.04"),
    MappingEntry("20.10.05 - INTERNET", RENAME, "20.10.05", "Âmbito alargado para 'e comunicações'."),
    MappingEntry("20.10.06 - SEGURANÇA E ALARME", RENAME, "20.10.06"),
    MappingEntry("20.10.07 - MENSALIDADE DE SOFTWARE", RECODE, "20.20.01"),
    MappingEntry("20.10.08 - MENSALIDADE CONTABILIDADE", RECODE, "20.20.02", "Código 20.10.08 duplicado no catálogo atual."),
    MappingEntry("20.10.08 - PASTOR PRESIDENTE", RECODE, "20.30.01", "Código 20.10.08 duplicado no catálogo atual."),
    MappingEntry("20.10.09 - PASTOR AUXILIAR", RECODE, "20.30.02"),
    MappingEntry("30.10.02 - MANUTENÇÃO PREDIAL", RECODE, "30.10.01"),
    MappingEntry("30.10.03 - MOBILIÁRIO", RECODE, "30.20.01"),
    MappingEntry("30.10.04 - CONVIVIOS", RECODE, "40.10.02"),
    MappingEntry("30.10.05 - DECORAÇÃO", RECODE, "30.30.01"),
    MappingEntry("30.10.06 - DESPESAS COM EVENTOS", RECODE, "40.10.01"),
    MappingEntry("30.10.07 - EQUIPAMENTO MUSICAL", RECODE, "30.20.02"),
    MappingEntry("30.10.08 - EQUIPAMENTOS ELETRONICO", RECODE, "30.20.03"),
    MappingEntry("30.10.09 - MATERIAL DE EXPEDIENTE", RECODE, "20.20.03"),
    MappingEntry("30.10.10 - MATERIAL DE LIMPEZA", RECODE, "20.20.04"),
    MappingEntry("30.10.11 - LICENCIAS DE SOFTWARE", RECODE, "30.30.02"),
    MappingEntry("30.10.12 - DESPESAS COM MINISTROS", RECODE, "40.20.01"),
    MappingEntry("30.10.13 - ALUGUEL DE MÁQUINAS, EQUIPAMENTOS E MOBÍLIAS", RECODE, "30.20.04",
                 "O nome atual inclui 'mobílias'; o novo não."),
    MappingEntry("30.10.14 - SEGUROS PREDIAL / TERCEIROS", RECODE, "20.20.05"),
    MappingEntry("30.10.15 - IMPOSTOS GOVERNAMENTAIS", RECODE, "20.50.01"),
    MappingEntry("30.10.16 - MÃO DE OBRA ESPECIALISADA", RECODE, "30.10.02"),
    MappingEntry("AÇÃO SOCIAL [ DEPARTAMENTO ]", RECODE, "50.10.01",
                 "Decisão 01/11/2026: recodificar se o portal permitir editar como centro de custo."),
    MappingEntry("DIACONATO [ DEPARTAMENTO ]", RECODE, "50.10.02", "Ver AÇÃO SOCIAL."),
    MappingEntry("DEPARTAMENTO INFANTIL [ DEPARTAMENTO ]", RECODE, "50.20.01", "Ver AÇÃO SOCIAL."),
    MappingEntry("GRUPO DE MÚSICA [ DEPARTAMENTO ]", RECODE, "50.30.01", "Ver AÇÃO SOCIAL."),
    MappingEntry("JOVENS OMEGA [ DEPARTAMENTO ]", RECODE, "50.40.01", "Ver AÇÃO SOCIAL."),
    MappingEntry("MULHERES COM PROPÓSITO [ DEPARTAMENTO ]", RECODE, "50.40.02", "Ver AÇÃO SOCIAL."),
    MappingEntry("SACERDOTES DA FAMÍLIA [ DEPARTAMENTO ]", RECODE, "50.40.03", "Ver AÇÃO SOCIAL."),
    MappingEntry("MISSÕES VERBO DA VIDA [ DEPARTAMENTO ]", RECODE, "50.50.01", "Ver AÇÃO SOCIAL."),
]


def label_migration(mapping: List[MappingEntry] = None) -> Dict[str, str]:
    """De/para de rótulos: rótulo atual -> rótulo novo, só para RENOMEAR/RECODIFICAR.

    Centros PRESERVAR, MANTER e REVISAO_MANUAL não entram: o nome continua válido.
    """
    result: Dict[str, str] = {}
    for entry in (MIGRATION_MAP if mapping is None else mapping):
        if entry.operation not in (RENAME, RECODE) or not entry.target_code:
            continue
        target = TARGET_BY_CODE.get(entry.target_code)
        if target and target.label != entry.current:
            result[entry.current] = target.label
    return result
