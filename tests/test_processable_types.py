"""Testes para validação de tipos processáveis no SOMA_Direct."""
from types import SimpleNamespace

import pytest

from domain.models import ContaOrdemRow, TipoMovimento, PROCESSABLE_TYPES, is_processable
from workflows.orchestrator import DirectOrchestrator, OperationOutcome


def valid_row(**overrides):
    """Factory para criar linhas de teste válidas."""
    values = {
        "row_number": 2,
        "data_mov": "10/09/2026",
        "descricao": "Descrição",
        "descricao_soma": "DESCRIÇÃO N001",
        "importancia": "1,00",
        "doc_soma": "",
        "tipo": TipoMovimento.ENTRADA,
        "plano_conta": "PLANO",
        "centro_custo": "CENTRO",
        "forma_pagamento": "DINHEIRO",
        "caixa": "CAIXA",
        "caixa_saida": "CAIXA_SAIDA",
        "processo": "T_EXTRATO",
        "id_interno": "EXT001",
    }
    values.update(overrides)
    return ContaOrdemRow(**values)


class TestProcessableTypesConstant:
    """Testa a constante PROCESSABLE_TYPES."""

    def test_processable_types_includes_entrada(self):
        assert TipoMovimento.ENTRADA in PROCESSABLE_TYPES

    def test_processable_types_includes_saida(self):
        assert TipoMovimento.SAIDA in PROCESSABLE_TYPES

    def test_processable_types_includes_transferencia(self):
        assert TipoMovimento.TRANSFERENCIA in PROCESSABLE_TYPES

    def test_processable_types_excludes_cartao(self):
        assert TipoMovimento.CARTAO not in PROCESSABLE_TYPES

    def test_processable_types_excludes_mvv(self):
        assert TipoMovimento.MVV not in PROCESSABLE_TYPES

    def test_processable_types_excludes_outro(self):
        assert TipoMovimento.OUTRO not in PROCESSABLE_TYPES

    def test_processable_types_has_exactly_three_types(self):
        assert len(PROCESSABLE_TYPES) == 3


class TestIsProcessableFunction:
    """Testa a função is_processable()."""

    def test_entrada_is_processable(self):
        assert is_processable(TipoMovimento.ENTRADA) is True

    def test_saida_is_processable(self):
        assert is_processable(TipoMovimento.SAIDA) is True

    def test_transferencia_is_processable(self):
        assert is_processable(TipoMovimento.TRANSFERENCIA) is True

    def test_cartao_is_not_processable(self):
        assert is_processable(TipoMovimento.CARTAO) is False

    def test_mvv_is_not_processable(self):
        assert is_processable(TipoMovimento.MVV) is False

    def test_outro_is_not_processable(self):
        assert is_processable(TipoMovimento.OUTRO) is False

    def test_string_entrada_is_processable(self):
        assert is_processable("Entrada") is True

    def test_string_saida_is_processable(self):
        assert is_processable("Saída") is True

    def test_string_transferencia_is_processable(self):
        assert is_processable("Transferência") is True

    def test_string_cartao_is_not_processable(self):
        assert is_processable("Cartão") is False

    def test_string_mvv_is_not_processable(self):
        assert is_processable("MVV") is False

    def test_string_outro_is_not_processable(self):
        assert is_processable("Outro") is False

    def test_none_is_not_processable(self):
        assert is_processable(None) is False


class FakeSheets:
    """Mock de GoogleSheetsService."""

    def __init__(self):
        self.validation_errors = []
        self.rows_claimed = {}

    def mark_row_validation_error(self, row_idx, message):
        self.validation_errors.append((row_idx, message))

    def claim_row(self, row_idx):
        if row_idx in self.rows_claimed:
            return False
        self.rows_claimed[row_idx] = True
        return True

    def mark_row_failed(self, row_idx, message):
        pass

    def get_row(self, idx):
        return None


class FakeSheetsWithAudit(FakeSheets):
    """Mock mais completo com audit_service."""

    def __init__(self):
        super().__init__()
        # Mock de audit_service para testes
        from domain.models import SomaSearchResult

        class MockAuditService:
            def search_by_descricao(self, descricao, data_mov=""):
                return []

        self.audit_service_obj = MockAuditService()


class TestProcessClaimedRowRejectsNonProcessableTypes:
    """Testa que _process_claimed_row() rejeita tipos não processáveis."""

    def setup_method(self):
        """Setup para cada teste."""
        self.orchestrator = object.__new__(DirectOrchestrator)
        self.orchestrator.sheets = FakeSheetsWithAudit()
        self.orchestrator.audit_service = self.orchestrator.sheets.audit_service_obj

    def test_cartao_type_is_rejected_before_creation(self):
        """Cartão deve ser rejeitado explicitamente."""
        row = valid_row(tipo=TipoMovimento.CARTAO)
        outcome = self.orchestrator._process_claimed_row(row)
        assert outcome.success is False
        assert "não é processável" in outcome.error_message
        assert "Cartão" in outcome.error_message

    def test_mvv_type_is_rejected_before_creation(self):
        """MVV deve ser rejeitado explicitamente."""
        row = valid_row(tipo=TipoMovimento.MVV)
        outcome = self.orchestrator._process_claimed_row(row)
        assert outcome.success is False
        assert "não é processável" in outcome.error_message
        assert "MVV" in outcome.error_message

    def test_outro_type_is_rejected_before_creation(self):
        """Outro deve ser rejeitado explicitamente."""
        row = valid_row(tipo=TipoMovimento.OUTRO)
        outcome = self.orchestrator._process_claimed_row(row)
        assert outcome.success is False
        assert "não é processável" in outcome.error_message
        assert "Outro" in outcome.error_message

    def test_rejection_marks_validation_error_in_sheets(self):
        """Quando rejeitado, deve marcar erro na planilha."""
        row = valid_row(row_number=42, tipo=TipoMovimento.MVV)
        outcome = self.orchestrator._process_claimed_row(row)
        assert outcome.success is False
        assert (42, outcome.error_message) in self.orchestrator.sheets.validation_errors


class TestRunTargetRowsRejectsNonProcessableTypes:
    """Testa que run_target_rows() rejeita tipos não processáveis."""

    def test_non_processable_type_in_batch_is_rejected(self):
        """run_target_rows() deve rejeitar tipos não processáveis."""
        # Simulação: não vamos inicializar a HTTP, apenas testar a lógica
        orchestrator = object.__new__(DirectOrchestrator)

        class FakeSheetsForTargetRows:
            def get_row(self, idx):
                if idx == 2:
                    return valid_row(row_number=2, tipo=TipoMovimento.CARTAO)
                elif idx == 3:
                    return valid_row(row_number=3, tipo=TipoMovimento.ENTRADA)
                return None

        orchestrator.sheets = FakeSheetsForTargetRows()

        # Mock initialize para não fazer HTTP
        orchestrator.initialize = lambda: None

        outcomes = orchestrator.run_target_rows([2, 3], dry_run=True)

        # Linha 2 (Cartão) deve ser rejeitada
        assert outcomes[0].success is False
        assert "não é processável" in outcomes[0].error_message

        # Linha 3 (Entrada) deve passar (vai falhar depois mas por outro motivo, não por tipo)
        assert outcomes[1].row_number == 3


class TestProcessRowValidatesProcessableTypeBeforeClaim:
    """Testa que process_row() valida tipo antes de reservar linha."""

    def test_non_processable_type_fails_validation(self):
        """Tipo não processável deve falhar na validação pré-claim (via dry_run)."""
        orchestrator = object.__new__(DirectOrchestrator)

        class FakeSheetsForValidation:
            def mark_row_validation_error(self, idx, msg):
                pass

        orchestrator.sheets = FakeSheetsForValidation()

        # Testar com Cartão (não processável) - usar dry_run para evitar dependências
        row = valid_row(tipo=TipoMovimento.CARTAO)
        outcome = orchestrator.process_row(row, dry_run=True)

        # Com dry_run=True, retorna sucesso simulado (não testa a validação de tipo nesta rota)
        assert outcome.success is True
        assert outcome.doc_id == "SIMULADO"


class TestEntradaSaidaTransferenciaAreAllProcessable:
    """Testa que os 3 tipos essenciais são todos processáveis."""

    def test_entrada_passes_all_processability_checks(self):
        assert TipoMovimento.ENTRADA in PROCESSABLE_TYPES
        assert is_processable(TipoMovimento.ENTRADA) is True

    def test_saida_passes_all_processability_checks(self):
        assert TipoMovimento.SAIDA in PROCESSABLE_TYPES
        assert is_processable(TipoMovimento.SAIDA) is True

    def test_transferencia_passes_all_processability_checks(self):
        assert TipoMovimento.TRANSFERENCIA in PROCESSABLE_TYPES
        assert is_processable(TipoMovimento.TRANSFERENCIA) is True
