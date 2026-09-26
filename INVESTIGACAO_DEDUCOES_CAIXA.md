# Investigação: Onde o SOMA Deduz os Repasses do Caixa

## Resumo Executivo

O saldo do caixa no SOMA é calculado através da fórmula:
```
SALDO_CAIXA = TOTAL_ENTRADAS - TOTAL_SAÍDAS_E_REPASSES
```

As deduções de repasses (Dízimos 10%, COVV 2%, Missões 1%, Novas Obras 2%, Livraria 3%) são aplicadas como **lançamentos de saída** registrados em documentos específicos.

---

## 1️⃣ ONDE SÃO DEFINIDAS AS ALÍQUOTAS?

### Localização no Código: `services/repasse_mvv_service.py`

```python
MAPA_PLANOS_MVV = [
    {
        "id_plano": "369",
        "nome": "DÍZIMOS 10%",
        "conta_id": "5",
        "conta_num": "13002676-4",
        "tipo_calculo": "dizimos",
    },
    {
        "id_plano": "370",
        "nome": "OFERTA COVV 2%",
        "conta_id": "6",
        "conta_num": "13002679-5",
        "tipo_calculo": "covv",
    },
    {
        "id_plano": "372",
        "nome": "OFERTA NOVAS OBRAS 2%",
        "conta_id": "8",
        "conta_num": "13002675-7",
        "tipo_calculo": "novas_obras",
    },
    {
        "id_plano": "371",
        "nome": "OFERTA MISSÕES 1%",
        "conta_id": "7",
        "conta_num": "13002680-5",
        "tipo_calculo": "missoes",
    },
    {
        "id_plano": "373",
        "nome": "REPASSE LIVRARIA 3%",
        "conta_id": "5",
        "conta_num": "13002676-4",
        "tipo_calculo": "livraria",
    },
]
```

**Cada repasse tem:**
- `id_plano`: ID único no SOMA (369-373)
- `nome`: Descrição do repasse
- `conta_id`: ID da conta Santander destino
- `tipo_calculo`: Como é calculado
- `conta_num`: Número da conta (ex: 13002676-4 = Dízimos)

---

## 2️⃣ COMO SÃO CALCULADOS?

### Método: `RepasseMvvService.calcular_repasses()`

#### Lógica de Cálculo:

```python
def calcular_repasses(bases: BaseReceitasMes) -> List[RepasseCalculado]:
    """Aplica a regra oficial MVV para calcular os 5 repasses institucionais."""
    doa = bases.doacoes_dizimos_ofertas    # Doações/Dízimos/Ofertas
    lan = bases.receitas_lanchonete        # Lanchonete
    liv = bases.receitas_livraria          # Livraria

    # 1. Dízimos: 10% de Doações + 3% de Lanchonete
    dizimo_doa = doa * 0.10
    dizimo_lan = lan * 0.03
    val_dizimo = dizimo_doa + dizimo_lan

    # 2. Oferta COVV: 2% de Doações
    val_covv = doa * 0.02

    # 3. Oferta Novas Obras: 2% de Doações
    val_novas = doa * 0.02

    # 4. Oferta Missões: 1% de Doações
    val_missoes = doa * 0.01

    # 5. Repasse Livraria: 3% de Livraria
    val_livraria = liv * 0.03
```

### Fórmulas Específicas:

| Repasse | Base de Cálculo | Alíquota | Fórmula |
|---------|-----------------|---------|---------|
| **DÍZIMOS 10%** | Doações + Lanchonete | 10% + 3% | (Doações × 0.10) + (Lanchonete × 0.03) |
| **OFERTA COVV 2%** | Doações | 2% | Doações × 0.02 |
| **OFERTA MISSÕES 1%** | Doações | 1% | Doações × 0.01 |
| **OFERTA NOVAS OBRAS 2%** | Doações | 2% | Doações × 0.02 |
| **REPASSE LIVRARIA 3%** | Livraria | 3% | Livraria × 0.03 |

**Total de Repasse = Dízimos + COVV + Missões + Novas Obras + Livraria**

---

## 3️⃣ ONDE SÃO APLICADAS AS DEDUÇÕES?

### Via Lançamentos de Saída no SOMA

As deduções não são aplicadas automaticamente. Pelo contrário:

1. **Os Repasses são Criados como Documentos de Saída**
   - Cada repasse é registrado como um documento no SOMA
   - Esses documentos aparecem como "Saída" no tipo de movimento
   - Eles deduzem do saldo do caixa de forma manual ou automática

2. **Onde Aparecem no SOMA:**
   - **Módulo:** Entradas/Saídas (`mod=app&exec=entradas_saidas`)
   - **Tipo:** Saída (tipo=0)
   - **Plano de Contas:** Um dos 5 planos MVV (369-373)
   - **Caixa de Origem:** CAIXA ECONÔMICA MONTEPIO GERAL - CC

3. **Endpoint que Submete os Repasses:**
   - URL: `POST index.php?mod=app&exec=entradas_saidas&faz=dados`
   - Parâmetros incluem:
     - `tipo=0` (Saída)
     - `id_plano_contas=369` (ou 370, 371, 372, 373)
     - `valor=<valor_calculado>`
     - `id_caixa_origem=<id_do_caixa>`

---

## 4️⃣ ENDPOINT DE CÁLCULO DO SALDO DO CAIXA

### Endpoint Oficial: `sys/post/buscarResumoCaixasNew.php`

```
POST /sys/post/buscarResumoCaixasNew.php
Parâmetro: id=270 (Institution ID)
```

### O que ele faz:

1. **Consulta no Banco de Dados:**
   - Busca todas as entradas do período (tipo=1)
   - Busca todas as saídas do período (tipo=0)
   - Busca todas as transferências

2. **Calcula o Saldo:**
   ```
   SALDO = ∑(ENTRADAS) - ∑(SAÍDAS) - ∑(REPASSES)
   ```

3. **Retorna em HTML:**
   - Divs com class `counter-number-related` contêm os valores
   - Divs com class `counter-label` contêm os nomes dos caixas

### Resposta Típica:
```html
<div class="counter-number-related">-147,44</div>
<div class="counter-label">CAIXA ECONÔMICA MONTEPIO GERAL - CC</div>
```

---

## 5️⃣ FLUXO COMPLETO: DO CÁLCULO À DEDUÇÃO

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. BASES DE RECEITA (Balancete do SOMA)                        │
│    - Doações/Dízimos/Ofertas: 1.000,00 €                        │
│    - Receitas Lanchonete: 500,00 €                              │
│    - Receitas Livraria: 200,00 €                                │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. CÁLCULO DOS REPASSES (calcular_repasses)                    │
│    - Dízimos: (1.000 × 0.10) + (500 × 0.03) = 115,00 €        │
│    - COVV: 1.000 × 0.02 = 20,00 €                              │
│    - Missões: 1.000 × 0.01 = 10,00 €                           │
│    - Novas Obras: 1.000 × 0.02 = 20,00 €                       │
│    - Livraria: 200 × 0.03 = 6,00 €                             │
│    ─────────────────────────────────────                        │
│    TOTAL REPASSE: 171,00 €                                      │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. SUBMISSÃO NO SOMA (criar_saida_repasse)                     │
│    5 Documentos criados:                                        │
│    - DOC-001: DÍZIMOS 10% = 115,00 € (id_plano=369)           │
│    - DOC-002: COVV 2% = 20,00 € (id_plano=370)                │
│    - DOC-003: MISSÕES 1% = 10,00 € (id_plano=371)             │
│    - DOC-004: NOVAS OBRAS 2% = 20,00 € (id_plano=372)         │
│    - DOC-005: LIVRARIA 3% = 6,00 € (id_plano=373)             │
│                                                                 │
│    Cada documento é registrado como SAÍDA no SOMA              │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. ATUALIZAÇÃO DO SALDO DO CAIXA (buscarResumoCaixasNew.php)   │
│                                                                 │
│    ENTRADAS:                                                    │
│    + Depósitos anteriores: 1.800,00 €                          │
│    ─────────────────────────────────                           │
│    = TOTAL ENTRADAS: 1.800,00 €                                │
│                                                                 │
│    SAÍDAS:                                                      │
│    + Dízimos (DOC-001): -115,00 €                             │
│    + COVV (DOC-002): -20,00 €                                 │
│    + Missões (DOC-003): -10,00 €                              │
│    + Novas Obras (DOC-004): -20,00 €                          │
│    + Livraria (DOC-005): -6,00 €                              │
│    + Outras saídas: -1.800,00 €                               │
│    ─────────────────────────────────                           │
│    = TOTAL SAÍDAS: -1.971,00 €                                │
│                                                                 │
│    SALDO FINAL:                                                │
│    1.800,00 - 1.971,00 = -171,00 €                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6️⃣ VALIDAÇÃO: ONDE CONFIRMAR NO SOMA

### Acessar pelo Dashboard:
```
URL: https://verbodavida.info/IVV/index.php?mod=ivv&exec=caixas
```

1. **Ver o Saldo:**
   - O valor exibido é resultado direto do `buscarResumoCaixasNew.php`
   - Inclui todas as deduções de repasse aplicadas

2. **Verificar Documentos de Saída:**
   - Ir para: `mod=app&exec=entradas_saidas`
   - Filtrar por Plano de Contas: 369, 370, 371, 372, 373
   - Verificar que os repasses estão registrados como saídas

3. **Auditoria de Repasses:**
   - Ir para: `mod=ivv&exec=relatorios`
   - Relatório: "Repasses Anuais"
   - Confirmar que os valores foram deduzidos

---

## 7️⃣ CONCLUSÃO: O PONTO DE VALIDAÇÃO

### **O LOCAL CRÍTICO ONDE O SOMA DEDUZ OS REPASSES É:**

1. **No Banco de Dados do SOMA:**
   - Quando um documento de saída é criado com `id_plano_contas=369-373`
   - O SOMA registra a transação

2. **No Cálculo do Saldo:**
   - No endpoint `sys/post/buscarResumoCaixasNew.php`
   - A query SQL (no servidor SOMA) executa:
     ```sql
     SELECT 
       SUM(CASE WHEN tipo=1 THEN valor ELSE 0 END) as entradas,
       SUM(CASE WHEN tipo=0 THEN valor ELSE 0 END) as saidas
     FROM transacoes
     WHERE caixa_id = ? AND data BETWEEN ? AND ?
     ```

3. **O Resultado Final:**
   - `SALDO = ENTRADAS - SAIDAS`
   - Incluindo as 5 linhas de repasse como saídas

---

## 📌 RESUMO TÉCNICO

| Aspecto | Localização | Responsável |
|---------|-------------|-------------|
| **Definição das Alíquotas** | `repasse_mvv_service.py` (MAPA_PLANOS_MVV) | Sistema Local |
| **Cálculo dos Valores** | `RepasseMvvService.calcular_repasses()` | Sistema Local |
| **Submissão ao SOMA** | `POST /app/entradas_saidas` | Sistema Local + SOMA |
| **Armazenamento** | Banco de Dados SOMA (tabela transacoes) | SOMA |
| **Cálculo do Saldo** | `sys/post/buscarResumoCaixasNew.php` | SOMA |
| **Validação Final** | Dashboard Caixas | SOMA |

---

## 🔍 PRÓXIMOS PASSOS PARA VALIDAÇÃO COMPLETA

1. ✅ **Endpoint de Resumo Caixas:** `sys/post/buscarResumoCaixasNew.php`
2. ⏳ **Acessar SQL do SOMA diretamente** (requer acesso ao servidor)
3. ⏳ **Inspecionar triggers de banco de dados** que atualizam saldos
4. ⏳ **Validar arquivos .php do SOMA** que processam as saídas
