# Validação de Transferências: Status Técnico e Próximos Passos

## 📋 Objetivo da Implementação

Validar todos os 151 registos de transferência da sheet CONTAORDEM contra as transferências registadas no portal SOMA, comparando:
- **Data do movimento** (DATA MOV.)
- **Valor** (IMPORTÂNCIA)
- **Caixa origem** (CAIXA SAIDA)
- **Caixa destino** (CAIXA)

Gravar os resultados (STATUS: VALIDADO/ERRO e diagnóstico) na coluna **TRANSFERENCIAS** da sheet CONTAORDEM.

---

## ✅ O QUE FOI FEITO

### 1. **Descoberta da API SOMA**
- **URL**: `POST /IVV/sys/post/buscarTransferenciasCaixas.php`
- **Parâmetros**: 
  - `id_inst`: vazio (todas as instituições)
  - `i`: data início (DD/MM/YYYY)
  - `f`: data fim (DD/MM/YYYY)
- **Headers**: `X-Requested-With: XMLHttpRequest`
- **Resposta**: HTML com tabela `<tr>...<td>...caixa_origem...valor_saida...caixa_destino...valor_entrada...data...</td>...</tr>`

### 2. **Correção da Normalização de Nomes de Caixas**
- **Ficheiro**: `domain/models.py` - função `clean_caixa()`
- **Antes**: Removia apenas sufixos entre `[]`
- **Depois**: Remove também sufixos `- CC` / `- CP` para normalizar variações
  - `"CAIXA ECONÔMICA MONTEPIO GERAL [CONTA CORRENTE]"` → `"caixa economica montepio geral"`
  - `"Caixa Econômica Montepio Geral - CC"` → `"caixa economica montepio geral"`

### 3. **Scripts de Validação Criados**

#### A. `list_transferencias_contaordem.py`
- Lista todas as 151 transferências da sheet CONTAORDEM

#### B. `inspect_transferencias_form.py`
- Inspeciona o DOM do site SOMA para descobrir campos do formulário

#### C. `teste_busca_transferencia_soma.py`
- Testa a busca de transferências com um registo real
- Resultado: Encontrou correspondência com coluna REPASSE já existente

#### D. `validar_transferencias_contra_soma.py`
- Valida todas as 151 transferências contra SOMA
- **Resultado**: 130 VALIDADAS (86.1%) | 21 ERROS (13.9%)

#### E. `validar_todos_transferencias.py`
- Executa validação completa com relatório detalhado
- Gera análise de tipos de erro

#### F. `gravar_*.py` (múltiplas versões)
- Tenta gravar resultados na sheet CONTAORDEM, coluna AA (TRANSFERENCIAS)
- **Problema**: API quota limits do Google Sheets
  - Tentativa 1: `batch_update()` - erro de estrutura JSON
  - Tentativa 2: `update()` - erro de formato de valores
  - Tentativa 3: `update_cell()` - gravou 82 de 151 registos antes de atingir quota

---

## 📊 Resultados da Validação (130 de 151 registos)

### ✅ Validadas (130 = 86.1%)
Todos os registos com:
- Data coincidente
- Valor coincidente
- Caixa origem coincidente
- Caixa destino coincidente

### ❌ Com Erros (21 = 13.9%)

#### Tipo 1: Não Encontrado no SOMA (13 registos)
- Datas: Abril/Maio 2026
- **Causa provável**: Transferências deletadas do SOMA ou período não buscado

#### Tipo 2: Data Divergente (7 registos)
- Diferença de 1-3 dias entre CONTAORDEM e SOMA
- **Causa**: Processamento assíncrono - transferência registada em data diferente
- Exemplos:
  - EXT0000002495: 06/01/2026 (CONTAORDEM) vs 04/01/2026 (SOMA)
  - EXT0000002336: 25/11/2025 vs 23/11/2025

#### Tipo 3: Origem Divergente (2 registos)
- Campo caixa origem vazio em SOMA
- **Causa**: Dados incompletos em SOMA ou deletados

---

## ❌ O QUE FALTA FAZER

### **Problema Principal: API Quota do Google Sheets**

A gravação via `update_cell()` individual atinge o limite de "Write requests per minute per user" e para depois de ~82 registos.

### **Soluções Possíveis (por ordem de recomendação)**

#### **Opção 1: Usar batch_update() corretamente (RECOMENDADO)**
```python
# Estrutura correta:
body = {
    "data": [
        {
            "range": "CONTAORDEM!AA2:AA10",
            "majorDimension": "ROWS",
            "values": [
                ["VALIDADO"],
                ["ERRO | Não encontrado"],
                ...
            ]
        }
    ],
    "valueInputOption": "USER_ENTERED"
}
spreadsheet.batch_update(body)
```
- Grava ~50 células por batch
- Delays de 2-3 segundos entre batches
- Evita quota limit

#### **Opção 2: Usar Google Sheets API diretamente (via gspread)**
```python
# Ao invés de worksheet.update_cell()
client = gspread.authorize(credentials)
sheet = client.open_by_key(spreadsheet_id)
worksheet = sheet.worksheet("CONTAORDEM")

# Batch update de ranges
worksheet.batch_update(
    [{"range": f"AA{row}", "values": [[valor]]} for row, valor in updates]
)
```

#### **Opção 3: Implementar retry com exponential backoff**
```python
import time

def update_with_retry(worksheet, row, col, valor, max_retries=3):
    for attempt in range(max_retries):
        try:
            worksheet.update_cell(row, col, valor)
            return True
        except APIError as e:
            if "429" in str(e):  # Too Many Requests
                wait_time = 2 ** attempt  # 2, 4, 8 segundos
                time.sleep(wait_time)
            else:
                raise
    return False
```

#### **Opção 4: Usar script via Google Apps Script (sem quota limits)**
- Criar macro no próprio Google Sheets
- Executar localmente na Google (menos sujeito a quotas)
- Requer acesso de desenvolvimento ao Google Sheets

---

## 🔧 Código Recomendado para Próxima Execução

**Ficheiro**: `scripts/gravar_batch_correto.py`

```python
def gravar_transferencias_batch(transferencias, worksheet):
    """Grava 151 registos em batches respeitando quotas."""
    
    col_number = 27  # AA
    batch_size = 50
    
    for i in range(0, len(transferencias), batch_size):
        batch_chunk = transferencias[i:i+batch_size]
        batch_data = []
        
        for update in batch_chunk:
            row_idx = update["row"]
            valor = f"{update['status']}"
            if update['soma_id']:
                valor += f" | {update['soma_id']}"
            if update['diagnostico']:
                valor += f" | {update['diagnostico']}"
            
            batch_data.append({"range": f"AA{row_idx}", "values": [[valor]]})
        
        # Batch update de uma vez
        spreadsheet.batch_update({
            "data": batch_data,
            "valueInputOption": "USER_ENTERED"
        })
        
        # Delay entre batches
        time.sleep(2)
```

---

## 📌 Ficheiros Principais Criados

| Ficheiro | Função |
|----------|---------|
| `scripts/list_transferencias_contaordem.py` | Lista 151 transferências |
| `scripts/validar_transferencias_contra_soma.py` | Valida contra SOMA |
| `scripts/validar_todos_transferencias.py` | Validação completa + relatório |
| `scripts/gravar_cells.py` | Tenta gravar (parcialmente bem-sucedido - 82/151) |
| `domain/models.py` (editado) | Melhorada função `clean_caixa()` |

---

## 🎯 Próximos Passos Imediatos

1. **Copiar lógica de validação** do `validar_todos_transferencias.py`
2. **Implementar batch_update() correto** com estrutura JSON adequada
3. **Testar com 10 registos primeiro** para validar a gravação
4. **Gravar em lotes de 50** com delays de 2-3 segundos
5. **Verificar na sheet** que coluna AA (TRANSFERENCIAS) tem os resultados

---

## 💾 Estado da Sheet CONTAORDEM

- **Coluna AA (TRANSFERENCIAS)**: 82 registos têm valores
- **Registos 83-151**: Ainda precisam de gravação

---

## 🚀 Comando para Retomar

Depois de implementar a correção do batch_update:

```bash
python scripts/gravar_batch_correto.py
```

Isto deve gravar os 151 registos completamente em ~2-3 minutos.
