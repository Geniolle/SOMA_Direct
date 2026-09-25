# SOMA Direct — Guia de Agentes e Mapa do Repositório

## Visão Geral
O **SOMA Direct** é o motor de alta performance para conciliação contábil, integração com o ERP/site SOMA e sincronização com Google Sheets (`AppTesouraria` e `AppVerboCafé`).

---

## Estrutura Principal de Código

- **Core & Autenticação**:
  - `core/auth.py`: Autenticação direta HTTP no SOMA com retenção de cookie de sessão.
  - `core/http_session.py`: Sessão HTTP resiliente com retry automático e SSL configurável.
- **Modelos de Domínio**:
  - `domain/models.py`: Modelos tipados (`ContaOrdemRow`, normalizadores de texto, manipuladores de sufixo `Nxxx`).
- **Serviços**:
  - `services/sheets_service.py`: Camada de comunicação com Google Sheets, registro desacoplado de origens (`OriginConfig`), sincronização de `DOC. SOMA` e batch updates.
  - `services/audit_service.py`: Consulta de lançamentos no SOMA por período (`search_by_periodo`) ou código (`search_by_codigo`).
  - `services/monthly_checklist_service.py`: Motor de validação do checklist mensal (Balancete x Fluxo x Origem).
  - `services/reconciliation_resolver.py`: Resolução automatizada e atualização de descrições e divergências no site SOMA.
- **Workflows e Orquestração**:
  - `workflows/monthly_checklist_orchestrator.py`: Orquestrador de auditoria mensal e validação da coluna `ORIGEM`.
  - `workflows/reconciliation_orchestrator.py`: Orquestrador principal de conciliação e criação de lançamentos.
- **Documentação de Regras**:
  - `docs/REGRAS_CONCILIACAO_E_SEQUENCIAIS.md`: Regras de sequenciais diários Nxxx, resolução de origens e governança trilateral.

---

## Procedimentos Críticos

1. **Sequenciais Diários (`Nxxx`)**:
   - Devem ser únicos por dia (`DATA MOV.`).
   - Sincronização obrigatória e trilateral: Site SOMA + `CONTAORDEM.DESCRIÇÃO SOMA` + `SOMA.DESCRIÇÃO`.
2. **Resolução de Origens**:
   - `PROCESSO → Spreadsheet → Sheet`.
   - Origens externas (ex: `AppVerboCafé`) são resolvidas dinamicamente via `OriginConfig`.
   - Nunca sobrescrever um `DOC. SOMA` existente divergente sem confirmação.
3. **Datas Contábeis**:
   - A data da planilha de Origem tem precedência absoluta sobre datas provisórias.

---

## Bateria de Testes
Executar testes com:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```
