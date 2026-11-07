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
  - `services/repasse_service.py`: Leitura e auditoria de repasses contra balancetes.
  - `services/repasse_mvv_service.py`: Cálculo oficial dos 5 repasses institucionais MVV e submissão direta no SOMA.
- **Workflows e Orquestração**:
  - `workflows/monthly_checklist_orchestrator.py`: Orquestrador de auditoria mensal e validação da coluna `ORIGEM`.
  - `workflows/reconciliation_orchestrator.py`: Orquestrador principal de conciliação e criação de lançamentos.
  - `workflows/repasse_mvv_orchestrator.py`: Orquestrador isolado do cálculo e gravação de Repasses MVV.
  - `workflows/repasse_mvv_cli.py` & `repasse_mvv.py`: CLI interativo para execução do Repasse MVV.
- **Documentação de Regras**:
  - `docs/REGRAS_CONCILIACAO_E_SEQUENCIAIS.md`: Regras de sequenciais diários Nxxx, resolução de origens e governança trilateral.
  - `docs/REGRAS_TRANSFERENCIAS.md`: Como a Transferência deve ficar — mapeamento do formulário SOMA, endpoints de gravação, `Transferido` no DOC. SOMA, duplicados e execução.

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
4. **Transferências** (detalhe em `docs/REGRAS_TRANSFERENCIAS.md`):
   - Gravar via `POST sys/app/transferencias_caixas.php` (JSON `status 1` = gravada). Nunca via a página `?mod=ivv&exec=transferencias_caixas_dados` — não grava nada.
   - Enviar sempre `id_cc_saida`/`id_cc_entrada` (CENTRO DE CUSTO; PADRÃO = `0`), como o Selenium do projeto SOMA.
   - Concluída → `DOC. SOMA = Transferido` na CONTAORDEM e na origem; ID da transferência em `DADOS DOC`. `TRF_#####` é inválido.
   - Pesquisar antes de criar (data + valor + caixas); nunca usar "a primeira transferência da tabela".
   - Entradas/Saídas seguem a mesma regra: `POST sys/app/entradas_saidas.php`, DOC. SOMA = `id` devolvido.
5. **Execução**:
   - Sessão única do orquestrador (`core/run_lock.py`); processar um registo de cada vez, voltar a procurar candidatos e terminar quando não houver mais.

---

## Bateria de Testes
Executar testes com:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```
