# Prompt — Reorganização dos Centros de Custos (SOMA + AppTesouraria)

> Prompt para um agente Claude Code a correr **no PC do utilizador**, na pasta
> `C:\workspace\SOMA_Direct`, com acesso à chave SSH e ao `.env` locais.
> Autorização do utilizador (01/11/2026): executar tudo — SOMA, CCVV e CONTAORDEM.

---

## Missão

Executar a reorganização do plano de centros de custos da instituição **BRAGA - PORTUGAL
(id_inst 270)** nesta ordem obrigatória:

1. **SOMA** (portal `https://verbodavida.info/IVV/?mod=ivv&exec=centro_custos`): renomear,
   recodificar e criar centros, pela ordem do DRY_RUN.
2. **CCVV** (folha da AppTesouraria): mesmas renomeações + centros novos.
3. **CONTAORDEM**: substituir na coluna `CENTRO DE CUSTO` os rótulos antigos pelos novos (de/para).

Nada de eliminar centros. Nada de alterar documentos financeiros, DOC. SOMA, STATUS ou outras colunas.

---

## Ambiente e caminhos

| Item | Valor |
|------|-------|
| Projeto local (Windows) | `C:\workspace\SOMA_Direct` |
| Chave SSH e ficheiros do servidor | `C:\workspace\SOMA_Direct\Servidor\` (chave `ssh-key-2026-08-30_v2.key`) |
| Servidor | `opc@145.241.202.16` (alias `servidor-tesouraria-v2`) |
| Projeto no servidor | `/home/opc/SOMA_Direct` |
| Python do servidor | `/home/opc/SOMA_Direct/.venv/bin/python` |
| Serviço de produção | `somadirect.service` (`agendador.py`, systemd) |
| Repositório | `https://github.com/Geniolle/SOMA_Direct` (**PÚBLICO**), branch `main` |
| Planilha | AppTesouraria `1poVWJGSBb13_2S1YKEzvFmkB9Ru0ZVzfQ0OEcMkfOZw` — folhas `CCVV` (gid 1106953994) e `CONTAORDEM` |

Comando SSH base (PowerShell):

```powershell
ssh -i "C:\workspace\SOMA_Direct\Servidor\ssh-key-2026-08-30_v2.key" -o IdentitiesOnly=yes opc@145.241.202.16
```

Antes de tudo: `cd C:\workspace\SOMA_Direct; git status; git pull origin main` (não fazer pull se houver
alterações locais — reportar). No servidor: `cd /home/opc/SOMA_Direct; git status; git pull --ff-only origin main`.

### Segurança (obrigatório)

- Credenciais vêm do `.env` (`SITE_USER`, `SITE_PASSWORD`, `GOOGLE_CREDENTIALS_PATH`, `INSTITUTION_ID`)
  através de `config.settings.Settings.from_env()`. **Nunca** imprimir, registar ou commitar
  credenciais, cookies, a chave SSH ou o `.env`.
- O repositório é **público**: nunca adicionar a pasta `Servidor\`, `.env` ou `*.key` ao git
  (confirmar que estão no `.gitignore`).
- Não editar o `.env`.

---

## Código já existente (main, PRs #5, #6, #7)

| Ficheiro | Função |
|----------|--------|
| `domain/cost_center_plan.py` | `TARGET_PLAN` (plano XX.XX.XX), `MIGRATION_MAP` (rótulo atual → operação/código), `label_migration()` (de/para) |
| `services/cost_center_service.py` | `CostCenter`, `parse_select_options`, `find_duplicates`, `build_dry_run` (identidade por ID+descrição, ordem por dependências, conflitos, idempotente), `summarize`. **Não escreve.** |
| `scripts/audit_cost_centers.py` | Auditoria só leitura do portal: página/forms/endpoints JS, inventário com IDs, `--usage` (lançamentos por centro), DRY_RUN, `--out` JSON. `--apply` recusado. |
| `scripts/migrate_cost_center_sheets.py` | CCVV + CONTAORDEM. DRY_RUN por omissão; `--apply` **recusa** enquanto o SOMA não tiver os rótulos novos; confirma por releitura. |
| `services/soma_api_service.py` | `resolve_centro_custo_id` já traduz rótulos antigos para os novos (de/para) — os lançamentos continuam a funcionar com nomes antigos vindos das folhas de origem. |
| `core/auth.py`, `core/http_session.py` | Login HTTP no SOMA (`SomaAuthenticator.login(force=True)`) e sessão (`ResilientSession.get/post/post_ajax`). Reutilizar. |
| `tests/test_cost_center_plan.py` | Testes do planeador, de/para e folhas. |

Correr testes: `.\.venv\Scripts\python.exe -m pytest tests/` (local) ou `.venv/bin/python -m pytest tests/ -q` (servidor). Estado atual: 226 passaram, 1 ignorado.

### Endpoints SOMA conhecidos

- Catálogo de centros: `POST sys/post/buscarCentrodeCustoSelect.php` `{id: 270}` → `<option value=ID>CÓDIGO - DESCRIÇÃO</option>` (PADRÃO = `0`).
- Pesquisa de lançamentos: `POST sys/post/buscarEntradasSaidas.php` (parâmetro `cc` = id do centro; `-1` = todos).
- Padrão de gravação do SOMA: `POST sys/app/<modulo>.php` via AJAX com os campos do formulário +
  `add=1` (criar) ou `id_<entidade>=<ID>` (editar); resposta JSON `{status: 1}` = sucesso
  (ver `docs/REGRAS_TRANSFERENCIAS.md`). **O endpoint de centros de custo ainda não foi confirmado**
  (provavelmente `sys/app/centro_custos.php`) — confirmar pela auditoria, nunca presumir.

---

## Inventário de partida (catálogo real, 46 centros) e decisões

- Duplicado: `20.10.08` em MENSALIDADE CONTABILIDADE e PASTOR PRESIDENTE.
- IDs conhecidos: PADRÃO = 0, `10.10.01 - DÍZIMOS E OFERTAS` = 6226, `10.10.02 - OFERTA ALÇADA` = 5609.
- Decisões do utilizador (já no `MIGRATION_MAP`):
  - **D1** `10.10.01 - DÍZIMOS E OFERTAS`: **PRESERVAR** (histórico intacto). Criar `10.10.02 - OFERTAS GERAIS`.
    O novo "10.10.01 - DÍZIMOS" fica pendente (código ocupado) — não criar.
  - **D2** `10.40.02 - SAÍDA DE EMPRÉSTIMO` → `60.10.02 - AMORTIZAÇÃO DE EMPRÉSTIMO`.
  - **D3** `… [ DEPARTAMENTO ]` (8) → `50.xx.xx` **só se o portal os mostrar como centros de custo editáveis**.
    Se forem outra entidade, não tocar e marcar no relatório; ajustar o `MIGRATION_MAP` (PRESERVAR) para que o
    passo das folhas não fique bloqueado.
- `PADRÃO` (ID 0): preservar sempre (usado por omissão e pelas Transferências).
- Grupos intermédios (`10.10`, `20.10`…): criar **só** se o portal tiver hierarquia real (campo "pai"); caso
  contrário não criar.

DRY_RUN atual (catálogo da CCVV): ~43 operações PRONTO + criações; ordem por dependências, por exemplo:
REPASSE MVV `10.20.01→40.30.01` antes de VERBO LIVRARIA `10.10.04→10.20.01`, antes de OFERTA ESPECIAL
`10.10.03→10.10.04`, antes de OFERTA ALÇADA `10.10.02→10.10.03`, e só então criar `10.10.02 - OFERTAS GERAIS`.

---

## Passos

### Passo 0 — Preparação
1. `git pull origin main` local e no servidor; correr os testes.
2. Parar o serviço durante a migração para não haver lançamentos a meio:
   `sudo systemctl stop somadirect.service` (confirmar `systemctl is-active` ≠ active).

### Passo 1 — Auditoria (só leitura)
```bash
cd /home/opc/SOMA_Direct
.venv/bin/python -u scripts/audit_cost_centers.py --usage --out /home/opc/cc_inventario.json
```
Analisar: estrutura da tabela, ações (editar/excluir/inativar), formulários de criação e edição (campos,
hidden inputs, selects — código, descrição, instituição, pai, status), endpoints JS de gravação e os trechos
de JS com o pedido e o tratamento da resposta (códigos de `status`). Confirmar se o filtro `cc` na contagem
de uso funciona (a contagem total não pode ser igual para todos os centros). Se a auditoria não bastar,
ler com `ResilientSession.get` a página de edição de um centro (`?mod=ivv&exec=centro_custos_dados&ID=<id>`)
e o JS `themes/js/centro_custos*.js`. Nada de vulnerabilidades, nada de tentar ler PHP do servidor.

### Passo 2 — Implementar a escrita (APPLY) no SOMA_Direct
Em `services/cost_center_service.py` (ou novo `services/cost_center_writer.py`), com mudanças mínimas:
- `create_cost_center(code, description)` e `update_cost_center(id, code, description)` usando **exatamente**
  o endpoint e os campos que o JavaScript do portal envia (mesmos nomes, hidden inputs, `id_inst=270`).
- Interpretar a resposta JSON (`status` 1 = sucesso; outros = falha com mensagem).
- Antes de cada escrita: reler o catálogo e confirmar que o registo (por **ID**) ainda tem o rótulo esperado e
  que o código de destino está livre (ou ocupado pelo próprio). Divergência → **parar** e reportar.
- Depois de cada escrita: reler o catálogo e confirmar o novo rótulo no mesmo ID (ou o novo ID na criação).
- Falha de comunicação após POST → reler o estado antes de qualquer nova tentativa (nunca repetir às cegas).
- Registar cada operação (ID, antes, depois, resultado) num log JSON em `/home/opc/cc_apply_log.json`.
- Script `scripts/apply_cost_centers.py`: DRY_RUN por omissão; `--apply` exige também `--confirm <hash do plano>`
  impresso pelo DRY_RUN (garante que se aplica exatamente o plano revisto). Executa só operações `PRONTO`,
  pela coluna `ordem`. Idempotente: reexecutar depois de uma interrupção continua de onde parou.
- Testes com mocks: criação/atualização idempotentes, conflito de código, divergência antes de escrever,
  falha HTTP, sessão expirada (status 7), confirmação posterior, reexecução após interrupção.
- `git` só com os ficheiros de código/testes; PR para `main`; merge; `git pull` no servidor.

### Passo 3 — Aplicar no SOMA
```bash
.venv/bin/python -u scripts/apply_cost_centers.py                      # DRY_RUN + hash
.venv/bin/python -u scripts/apply_cost_centers.py --apply --confirm <hash>
.venv/bin/python -u scripts/audit_cost_centers.py --out /home/opc/cc_pos.json   # verificação
```
Confirmar no fim: nenhum código duplicado, todos os rótulos do plano presentes (exceto pendentes D1/D3),
nenhum centro eliminado, IDs com histórico intactos.

### Passo 4 — Folhas CCVV e CONTAORDEM
```bash
.venv/bin/python -u scripts/migrate_cost_center_sheets.py            # DRY_RUN
.venv/bin/python -u scripts/migrate_cost_center_sheets.py --apply
```
Só toca na coluna A da CCVV e na coluna `CENTRO DE CUSTO` da CONTAORDEM. Confirmar "Restam … 0".

### Passo 5 — Religar e validar
1. `sudo systemctl start somadirect.service`; ver `journalctl -u somadirect.service -n 80`.
2. Confirmar que um lançamento novo resolve o centro (sem "Centro de custo não encontrado").

---

## Regras inegociáveis

- Identidade = **ID interno + instituição + descrição**; nunca só o código.
- Não eliminar centros; não reutilizar um ID para um significado diferente; não alterar documentos financeiros.
- Ordem por dependências reais (quem liberta um código antes de quem o ocupa); ciclos = parar.
- Qualquer resposta inesperada → parar e reportar com evidência (sem segredos).
- Não alterar outras folhas, pagamentos, baixas, transferências nem parâmetros da instituição.

## Relatório final

Para cada operação: ordem, ID interno, código/descrição antes, código/descrição depois, operação, resultado
(OK / FALHOU / PULADO) e evidência de confirmação. Totais: SOMA (renomeados, recodificados, criados,
pendentes), CCVV (renomeados, acrescentados), CONTAORDEM (células alteradas por de/para), testes (X/X),
commits/PRs, estado final do serviço. Pendentes explícitos: D1 (novo 10.10.01 DÍZIMOS) e D3 se aplicável.
