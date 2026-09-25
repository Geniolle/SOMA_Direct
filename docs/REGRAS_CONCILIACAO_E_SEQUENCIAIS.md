# Conhecimento do Processo: Conciliação, Sequenciais e Origens (SOMA Direct)

Este documento formaliza as regras de negócio, a arquitetura técnica e os procedimentos operacionais para a governança de lançamentos, sequenciais e sincronização trilateral entre o **Site SOMA**, a planilha **CONTAORDEM** e as planilhas de **Origem** (`AppTesouraria` e `AppVerboCafé`).

---

## 1. Regra de Sequenciais e Textos Semelhantes (`Nxxx`)

### 1.1 Objetivo e Escopo
Garantir que todos os lançamentos que compartilham a mesma data e a mesma natureza de texto possuam uma discriminação sequencial única (`N001`, `N002`, `N003`, etc.).
- A unicidade do sequencial é estritamente **diária** (circunscrita a cada `DATA MOV.`).
- Não podem existir duas descrições idênticas na mesma data na `CONTAORDEM` nem no SOMA.

### 1.2 Algoritmo de Resolução de Sequenciais
1. **Normalização do Texto Base**:
   - Extrai o prefixo de data (se houver): `strip_date_prefix(desc)`.
   - Remove o sufixo numérico existente (`N\d{3}`): `strip_suffix_n(desc)`.
   - Normaliza caracteres (remoção de acentos e espaços extras): `norm_basic(base)`.
2. **Consulta de Semelhantes no SOMA**:
   - Consulta o SOMA no dia correspondente: `audit.search_by_periodo(data)`.
   - Localiza lançamentos com texto semelhante via `similar_base(base, item.descricao)`.
   - Coleta todos os números sequenciais já atribuídos no SOMA no dia.
3. **Alocação de Sequencial Único**:
   - **Manutenção**: Se o registo já possui um sequencial `Nxxx` válido e não colidente (isto é, existe no máximo uma vez no dia e pertence a este mesmo documento), o sequencial é mantido.
   - **Novo Sequencial**: Se o sequencial estiver ausente ou colidir com outro lançamento do mesmo dia, o sistema calcula o menor número inteiro positivo `next_available` (iniciando em 1) que não conste dos sequenciais já usados no SOMA nem dos já alocados no lote em processamento.
   - Formata a nova descrição como: `{base_limpa} N{seq:03d}`.

### 1.3 Sincronização Trilateral
A alteração de descrição é aplicada de forma atómica e coordenada em três pontos:
1. **Site SOMA**:
   - Envio de formulário HTTP via `POST ?mod=app&exec=entradas_saidas&faz=dados`.
   - Revalidação imediata com `search_by_codigo` para confirmar persistência no servidor SOMA antes de tocar nas planilhas.
2. **Sheet CONTAORDEM**:
   - Atualização da coluna `DESCRIÇÃO SOMA` da linha correspondente (`batch_update`).
3. **Sheet SOMA**:
   - Localização da linha pelo campo `CODIGO` e atualização da coluna `DESCRIÇÃO` (`batch_update`).

---

## 2. Resolução Dinâmica de Origens (`PROCESSO → Spreadsheet → Sheet`)

### 2.1 Arquitetura Desacoplada
Para evitar referências hardcoded, a resolução de origens é mediada pelo registro central `OriginConfig`:
- **Internas (AppTesouraria)**:
  - `T_EXTRATO` → Sheet `T_EXTRATO` no spreadsheet padrão.
  - `DÍZIMOS/OFERTAS` → Sheet `DÍZIMOS/OFERTAS` no spreadsheet padrão.
  - `SAÍDAS` → Sheet `SAÍDAS` no spreadsheet padrão.
- **Externas (AppVerboCafé)**:
  - `Financeiro` → Sheet `Financeiro` no spreadsheet externo (`EXTERNAL_SOURCE_SPREADSHEET_URL`).
  - `VC_VENDAS` → Sheet `VC_VENDAS` no spreadsheet externo (`EXTERNAL_SOURCE_SPREADSHEET_URL`).

### 2.2 Sincronização Reversa de `DOC. SOMA`
Após a criação ou identificação de um lançamento no site do SOMA:
1. O número `DOC. SOMA` é registado na `CONTAORDEM`.
2. A partir da linha da `CONTAORDEM`, recupera-se `PROCESSO`, `ID_INTERNO` e `DOC. SOMA`.
3. A resolução `PROCESSO → OriginConfig` abre o spreadsheet correto (mesmo externo) e a sheet correta.
4. Localiza o registo pela chave `ID_INTERNO`.
5. Validação de integridade:
   - Se o campo `DOC. SOMA` na origem estiver vazio, grava o valor.
   - Se já estiver preenchido com o mesmo valor, valida.
   - Se contiver um valor diferente, interrompe e sinaliza divergência (nunca sobrescreve silenciosamente).

---

## 3. Regra de Ajuste de Datas Divergentes

### 3.1 Precedência da Origem
A data registada na planilha de **Origem** (`DATA VALOR`, `DATA MOV.` ou `DATA`) representa o facto contábil real e tem **precedência absoluta** sobre a data lançada provisoriamente na `CONTAORDEM` ou no SOMA.

### 3.2 Procedimento no SOMA
Quando houver divergência de data:
1. **Alteração do Vencimento**: Atualiza `data_vencimento` e `data_entrada` no SOMA via `POST ?mod=app&exec=entradas_saidas&faz=dados`.
2. **Anulação do Pagamento e Baixa**: No SOMA, um lançamento pago não permite alteração direta da data do movimento financeiro. Portanto:
   - Exclui a baixa existente em `/sys/app/baixas.php`.
   - Exclui o pagamento existente em `/sys/app/pagamentos.php`.
3. **Novo Pagamento e Nova Baixa**:
   - Cria novo pagamento com a data correta em `/sys/app/pagamentos.php`.
   - Executa nova baixa com a data correta em `/sys/app/baixas.php`.
4. **Sincronização nas Planilhas**:
   - Atualiza `DATA MOV.` na `CONTAORDEM`.
   - Atualiza `PAGAMENTO` / `DATA` na sheet `SOMA`.

---

## 4. Scripts e Comandos Operacionais

| Operação | Comando | Descrição |
| :--- | :--- | :--- |
| **Auditoria da Coluna ORIGEM** | `.\.venv\Scripts\python.exe scripts/list_origem_errors.py` | Agrupa e quantifica os erros remanescentes na coluna `ORIGEM`. |
| **Recálculo da Coluna ORIGEM** | `.\.venv\Scripts\python.exe workflows/monthly_checklist_cli.py --validar-origem` | Reavalia todas as linhas e regrava a coluna `ORIGEM` na `CONTAORDEM`. |
| **Resolução de Sequenciais Ausentes** | `.\.venv\Scripts\python.exe scripts/resolve_missing_sequence_descriptions.py --apply` | Atribui sequencial para itens sem sufixo `Nxxx`. |
| **Resolução de Sequenciais Duplicados** | `.\.venv\Scripts\python.exe scripts/apply_resolve_duplicates.py --apply` | Resolve colisões do mesmo dia, atualizando SOMA, CONTAORDEM e sheet SOMA. |
| **Correção de Data Divergente** | `.\.venv\Scripts\python.exe scripts/apply_origin_date_correction.py --doc <DOC> --new-date <DD/MM/AAAA>` | Anula pagamento, atualiza vencimento e refaz baixa com a data da origem. |
