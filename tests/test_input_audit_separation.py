"""Testes para validar separação rígida entre INPUT e AUDITORIA."""
import inspect

import pytest

from services.sheets_service import GoogleSheetsService
from services.audit_service import AuditService
from workflows.orchestrator import DirectOrchestrator


class TestInputDoesNotWriteAuditoria:
    """INPUT não deve escrever em AUDITORIA."""

    def test_mark_row_completed_no_auditoria_reference(self):
        """mark_row_completed() não deve referenciar AUDITORIA."""
        sheets = object.__new__(GoogleSheetsService)
        source = inspect.getsource(sheets.mark_row_completed)

        # Verificar que "AUDITORIA" não está no código (exceto docstring)
        code_lines = [line for line in source.split('\n') if not line.strip().startswith('#') and not line.strip().startswith('"""')]
        for line in code_lines:
            assert 'AUDITORIA' not in line, f"mark_row_completed() tem referência proibida: {line}"

    def test_mark_row_failed_no_auditoria_reference(self):
        """mark_row_failed() não deve referenciar AUDITORIA."""
        sheets = object.__new__(GoogleSheetsService)
        source = inspect.getsource(sheets.mark_row_failed)

        code_lines = [line for line in source.split('\n') if not line.strip().startswith('#') and '"""' not in line]
        for line in code_lines:
            assert 'AUDITORIA' not in line, f"mark_row_failed() tem referência proibida: {line}"

    def test_mark_row_validation_error_no_auditoria_reference(self):
        """mark_row_validation_error() não deve referenciar AUDITORIA."""
        sheets = object.__new__(GoogleSheetsService)
        source = inspect.getsource(sheets.mark_row_validation_error)

        code_lines = [line for line in source.split('\n') if not line.strip().startswith('#') and '"""' not in line]
        for line in code_lines:
            assert 'AUDITORIA' not in line, f"mark_row_validation_error() tem referência proibida: {line}"

    def test_mark_row_duplicate_no_auditoria_reference(self):
        """mark_row_duplicate() não deve referenciar AUDITORIA."""
        sheets = object.__new__(GoogleSheetsService)
        source = inspect.getsource(sheets.mark_row_duplicate)

        code_lines = [line for line in source.split('\n') if not line.strip().startswith('#') and '"""' not in line]
        for line in code_lines:
            assert 'AUDITORIA' not in line, f"mark_row_duplicate() tem referência proibida: {line}"


class TestAuditoriaDoesNotWriteStatus:
    """AUDITORIA não deve escrever em STATUS."""

    def test_audit_all_no_status_writing(self):
        """audit_all() não deve adicionar 'status' aos updates."""
        audit = object.__new__(AuditService)
        source = inspect.getsource(audit.audit_all)

        # Procurar padrões que indicam escrita em status
        assert '"status":' not in source, "audit_all() tem 'status': em updates"
        assert "'status':" not in source, "audit_all() tem 'status': em updates"

    def test_revalidate_inconsistencies_no_status_writing(self):
        """revalidate_inconsistencies() não deve adicionar 'status' aos updates."""
        audit = object.__new__(AuditService)
        source = inspect.getsource(audit.revalidate_inconsistencies)

        assert '"status":' not in source, "revalidate_inconsistencies() tem 'status': em updates"
        assert "'status':" not in source, "revalidate_inconsistencies() tem 'status': em updates"

    def test_audit_target_rows_no_status_calculation(self):
        """audit_target_rows() não deve calcular status."""
        orch = object.__new__(DirectOrchestrator)
        source = inspect.getsource(orch.audit_target_rows)

        assert 'status_str' not in source, "audit_target_rows() tem cálculo de status"

    def test_batch_update_audit_records_no_status_handling(self):
        """batch_update_audit_records() não deve processar 'status'."""
        sheets = object.__new__(GoogleSheetsService)
        source = inspect.getsource(sheets.batch_update_audit_records)

        assert 'upd.get("status")' not in source, "batch_update_audit_records() tem tratamento de status"

    def test_mark_row_audit_no_status_parameter(self):
        """mark_row_audit() não deve ter parâmetro 'status'."""
        sheets = object.__new__(GoogleSheetsService)
        sig = inspect.signature(sheets.mark_row_audit)
        params = list(sig.parameters.keys())

        assert "status" not in params, f"mark_row_audit() tem parâmetro status: {params}"


class TestReconciliationDoesNotWriteStatus:
    """Funções de reconciliação não devem escrever STATUS."""

    def test_reconcile_scheduled_descriptions_no_status(self):
        """reconcile_scheduled_descriptions() não deve escrever 'status'."""
        orch = object.__new__(DirectOrchestrator)
        source = inspect.getsource(orch.reconcile_scheduled_descriptions)

        assert '"status":' not in source, "reconcile_scheduled_descriptions() tem 'status' nos updates"

    def test_harmonize_preserves_status(self):
        """harmonize_sequentials_and_duplicates() não deve limpar STATUS."""
        sheets = object.__new__(GoogleSheetsService)
        source = inspect.getsource(sheets.harmonize_sequentials_and_duplicates)

        # Verificar que não há "status": ""
        assert 'row_upd["status"]' not in source, "harmonize() tem row_upd['status']"
        assert "row_upd['status']" not in source, "harmonize() tem row_upd['status']"


class TestArchitectureGuarantees:
    """Garantias arquiteturais finais."""

    def test_input_methods_cannot_write_auditoria(self):
        """Todos os métodos INPUT têm proteção contra AUDITORIA."""
        sheets = object.__new__(GoogleSheetsService)

        input_methods = [
            'mark_row_completed',
            'mark_row_failed',
            'mark_row_validation_error',
            'mark_row_duplicate',
        ]

        for method_name in input_methods:
            method = getattr(sheets, method_name)
            source = inspect.getsource(method)

            # Verificar que nenhum método INPUT refere AUDITORIA
            code_only = '\n'.join([
                line for line in source.split('\n')
                if not line.strip().startswith('#') and '"""' not in line
            ])

            assert 'AUDITORIA' not in code_only, \
                f"{method_name}() tem referência a AUDITORIA"

    def test_audit_methods_cannot_write_status(self):
        """Todos os métodos de AUDITORIA têm proteção contra STATUS."""
        audit = object.__new__(AuditService)

        audit_methods = [
            'audit_all',
            'revalidate_inconsistencies',
        ]

        for method_name in audit_methods:
            if not hasattr(audit, method_name):
                continue

            method = getattr(audit, method_name)
            source = inspect.getsource(method)

            # Verificar que nenhum método auditoria tenta escrever status
            assert '"status":' not in source and "'status':" not in source, \
                f"{method_name}() tem 'status' nos updates"
