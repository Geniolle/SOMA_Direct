# Transferências entre Caixas — Regras e Mapeamento SOMA

Referência oficial de como o SOMA Direct lança **Transferências** (e o que partilha com
Entradas/Saídas). Validado em produção em 27/09/2026 (commit `b8be2e3`).

---

## 1. Fluxo

```
CONTAORDEM (TIPO = Transferência, DOC. SOMA vazio)
   ↓  agendador.py → DirectOrchestrator.run_pending()
   ↓  sessão única (core/run_lock.py) — outra execução ativa = abortar
   ↓  procura candidatos → processa UM → volta a procurar → termina sem candidatos
   ↓  relê a linha antes de lançar (DOC. SOMA ainda vazio? mesmo ID_INTERNO?)
   ↓  pesquisa preventiva no SOMA (duplicados)
   ↓  SomaApiService.criar_transferencia(row)
SOMA  (POST sys/app/transferencias_caixas.php → JSON status 1)
   ↓
CONTAORDEM.DOC. SOMA = "Transferido"   CONTAORDEM.STATUS = "VALIDADO"
ORIGEM (ex.: T_EXTRATO).DOC. SOMA = "Transferido"
DADOS DOC = "Transferência <ID> de <CAIXA SAIDA> para <CAIXA> registada no SOMA."
```

Só `Entrada`, `Saída` e `Transferência` são processáveis. `Cartão`, `MVV` e `Outro`
nunca chegam a nenhuma função de criação.

---

## 2. Mapeamento do formulário (igual ao Selenium do projeto SOMA)

Fonte: JavaScript oficial `themes/js/transferencias_caixas_dados.js` e
`Geniolle/SOMA` → `transferencias_page.py` / `locators.json`.

| Campo SOMA                     | Origem na CONTAORDEM                     | Notas |
|--------------------------------|------------------------------------------|-------|
| `id_caixa_origem`              | `CAIXA SAIDA`                            | ID pelo catálogo de caixas (ex.: Caixa Diário = 1230) |
| `id_cc_saida`                  | `CENTRO DE CUSTO` (vazio → `PADRÃO`)     | **PADRÃO = `0`** (`buscarCentrodeCustoSelect.php`) |
| `valor_transferencia`          | `IMPORTÂNCIA`                            | formato `130,00` |
| `id_caixa_destino`             | `CAIXA`                                  | sufixo `[CONTA CORRENTE]` ignorado (Montepio = 1226) |
| `id_cc_entrada`                | `CENTRO DE CUSTO` (vazio → `PADRÃO`)     | mesmo valor de `id_cc_saida` |
| `valor_transferencia_entrada`  | `IMPORTÂNCIA`                            | igual ao valor de saída |
| `data_transferencia`           | `DATA MOV.`                              | `dd/mm/aaaa` |
| `obs`                          | `DESCRIÇÃO SOMA` (ou `DESCRIÇÃO`)        | |
| `id_inst`                      | `INSTITUTION_ID` (.env)                  | 270 = BRAGA - PORTUGAL |
| `id_transferencia_caixa`       | vazio                                    | vazio = nova transferência |
| `add`                          | `1`                                      | |
| `aceitar_caixa_negativo`       | `1`                                      | decisão do utilizador (27/09/2026) |

### Endpoint de gravação

- **Correto:** `POST sys/app/transferencias_caixas.php` (AJAX, resposta JSON).
- **Errado (não usar):** `?mod=ivv&exec=transferencias_caixas_dados` — é a **página** do
  formulário; o SOMA responde HTTP 200 com a página inicial e **não grava nada**.

### Resposta do SOMA

| `status` | Significado                                     | Resultado na CONTAORDEM |
|----------|-------------------------------------------------|-------------------------|
| 1        | A transferência foi salva                       | `Transferido` / VALIDADO |
| 2        | Nenhuma operação foi realizada                  | EM ERRO |
| 5        | Caixa sem o valor solicitado                    | EM ERRO |
| 6        | Mês fechado                                     | EM ERRO |
| 7        | Sessão expirada                                 | EM ERRO |
| 11       | Tornaria o caixa negativo                       | EM ERRO |
| sem JSON | Resposta inválida (ex.: página HTML)            | EM ERRO |

`status 1` é a confirmação oficial: mesmo que o ID não seja identificado na pesquisa
posterior, a linha fica `Transferido` (repetir criaria duplicado).

---

## 3. DOC. SOMA

- Transferência concluída (criada **ou** encontrada no SOMA) grava **`Transferido`** no
  `DOC. SOMA` da CONTAORDEM **e** da origem.
- O ID da transferência fica em `DADOS DOC`; não existe LINK (o link `?ID=` é só de
  Entradas/Saídas).
- `TRF_#####` **não** é um valor válido. Foi gravado por versões com defeito
  (`TRF_149817` em várias linhas sem transferência real) e é tratado como pendente.

---

## 4. Pesquisa e prevenção de duplicados

- Pesquisa: `POST sys/post/buscarTransferenciasCaixas.php` com
  `{id_inst, i: dd/mm/aaaa, f: dd/mm/aaaa}`; devolve **no máximo 100 linhas**.
  O ID vem do botão `bnt_excluir`; as 5 últimas células são origem, valor saída,
  destino, valor entrada, data (`domain.models.parse_transfer_table`).
- Antes de criar, pesquisa a **data** da linha:
  - 1 transferência com mesma data + valor + caixas → reutiliza (grava `Transferido`, não cria);
  - transferências com mesma data e valor mas caixas diferentes → `Analisar`, não cria;
  - nenhuma → cria.
- Nunca usar "a primeira transferência da tabela" como resultado (bug antigo `TRF_149817`).
- Nunca repetir um lançamento sem pesquisar primeiro: um erro depois do POST pode
  significar que o SOMA já gravou.

---

## 5. Execução

- **Automática:** serviço `somadirect` (`agendador.py`). Cada ronda processa todos os
  candidatos, um de cada vez, e termina quando não há mais; nova ronda a cada 60 s.
- **Manual (servidor):**
  ```bash
  cd /home/opc/SOMA_Direct
  .venv/bin/python main.py 38          # só a linha 38
  .venv/bin/python main.py --pending   # todos os pendentes, um a um
  ```
- **Sessão única:** com o serviço a processar, uma execução manual aborta com
  "O orquestrador já tem uma sessão ativa". Não correr cópias antigas do código
  (ex.: `C:\workspace\SOMA_Direct` desatualizado) — fazer `git pull origin main` antes.
- Cada ronda renova o login no SOMA (sessão PHP expira).

---

## 6. Entradas/Saídas (mesma lição)

- Gravação: `POST sys/app/entradas_saidas.php` (AJAX, JSON). `status 1` + `id` = DOC. SOMA.
- `FORMA DE PAGAMENTO` pelo catálogo `buscarFormaPagamento.php`:
  0 DINHEIRO, 1 DEPÓSITO, 2 CHEQUE, 3 TRANSFERÊNCIA BANCÁRIA, 4 PIX, 5 MAQUINETA,
  2210 TPA - CULTO/DEBITO, 2211 TPA - LIVRARIA/DEBITO.
- `?mod=app&exec=entradas_saidas&faz=dados` é a página, não grava.

---

## 7. Diagnóstico (só leitura)

| Script | Uso |
|--------|-----|
| `scripts/count_soma_transfers.py 2024 2026 [dd/mm/aaaa ...]` | transferências no SOMA por mês / por dia |
| `scripts/diagnose_transfer_search.py dd/mm/aaaa` | testa a pesquisa com vários intervalos/formatos |
| `scripts/discover_soma_save_endpoints.py` | lê formulários/JS do SOMA: campos, endpoints, catálogos |

Logs: `sudo journalctl -u somadirect.service -f`. Falhas de gravação registam
`resposta=<JSON>` e um resumo da resposta HTTP (status, URL, excerto).

---

## 8. Histórico

- Até 27/09/2026 **nenhuma transferência de 2024–2026 existia no SOMA**: o POST ia para a
  página do formulário e faltavam `id_cc_saida`/`id_cc_entrada`. Os `Transferido`/`TRF_`
  antigos nas folhas não correspondiam a documentos reais.
- Correção: PR #1 (pesquisa estrita, `Transferido`, sessão única, ciclo) e PR #2
  (endpoint `sys/app/…`, centros de custo, formas de pagamento).
