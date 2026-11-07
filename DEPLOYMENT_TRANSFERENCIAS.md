# 🚀 Deployment: Processamento de Transferências - SOMA Direct

**Data:** 26 de setembro de 2026  
**Commit:** fc195a5 - Fix: Transferências agora processadas automaticamente a cada 1 minuto

---

## 📋 Resumo das Alterações

### ✅ O Que Foi Corrigido

| # | Problema | Solução | Ficheiro |
|---|----------|--------|----------|
| 1 | Transferências bloqueadas em `run_target_rows()` | Removido filtro `only_entrada_saida` | `workflows/orchestrator.py` |
| 2 | Validação rejeitava Transferências | Adaptada para aceitar tipo correto | `workflows/orchestrator.py` |
| 3 | `_find_transfer_id()` não encontrava | Expandido intervalo 2000-2099 | `services/soma_api_service.py` |
| 4 | `mark_row_completed()` rejeitava `TRF_` | Aceita formato `TRF_#####` | `services/sheets_service.py` |
| 5 | Bug: passava `existing_id` sem prefixo | Corrigido para passar `doc_id` completo | `workflows/orchestrator.py` |

### 🧪 Testes Validados

✅ Transferências criadas manualmente (2454, 3736) agora são encontradas  
✅ Sistema processa ambas com sucesso no teste local  
✅ Agendador executa a cada 60 segundos sem erros

---

## 🔄 Fluxo de Deployment

### Passo 1: Pull das Alterações no Servidor

```bash
cd /caminho/para/SOMA_Direct
git pull origin main

# Verificar que pull foi bem-sucedido
git log --oneline -1  # Deve mostrar: fc195a5 Fix: Transferências...
```

### Passo 2: Reiniciar o Agendador

**Se está a correr em segundo plano:**
```bash
# Encontrar PID
ps aux | grep agendador.py

# Matar processo antigo
kill -9 <PID>

# Iniciar novo
nohup python agendador.py > logs/agendador.log 2>&1 &
```

**Se está a correr em serviço Windows:**
```powershell
# Parar serviço
Stop-Service -Name "SOMAdirect-Scheduler" -Force

# Aguardar 5s
Start-Sleep -Seconds 5

# Iniciar serviço
Start-Service -Name "SOMAdirect-Scheduler"

# Verificar status
Get-Service -Name "SOMAdirect-Scheduler"
```

### Passo 3: Monitorar Primeira Execução (LOCAL - no seu PC)

```bash
# Na sua máquina local:
cd C:\workspace\SOMA_Direct
python scripts/monitorar_primeira_execucao.py

# Isto vai:
# 1. Mostrar registos em aberto ANTES
# 2. Aguardar 2 minutos (primeira execução do agendador)
# 3. Mostrar registos em aberto DEPOIS
# 4. Verificar Transferências específicas (2454, 3736)
# 5. Validar se sistema está funcionando
```

---

## 📊 O Que Esperar

### Primeira Execução (1-2 minutos após restart)

**Esperado:** Os logs mostram algo como:
```
[INFO] --- [CICLO AGENDADO] Iniciando ronda em 26/09/2026 14:30:45 ---
[INFO] [CICLO AGENDADO] Concluído: 2 itens processados (2 sucesso, 0 falha).
[INFO] Aguardando 60s para a próxima verificação...
```

**Planilha após primeira execução:**
- ✅ Linha 2454: STATUS = "VALIDADO", DOC.SOMA = "TRF_295551"
- ✅ Linha 3736: STATUS = "VALIDADO", DOC.SOMA = "TRF_295552"

---

## 🔍 Como Monitorar

### Ver Logs em Tempo Real

```bash
# No servidor:
tail -f logs/soma_direct_$(date +%Y%m%d).log

# Procurar por Transferências:
grep -i "transferencia" logs/soma_direct_*.log | tail -20
```

### Verificar Planilha Google Sheets

1. Abrir: https://docs.google.com/spreadsheets/d/[seu_ID]/edit
2. Ir para sheet "CONTAORDEM"
3. Procurar pelas linhas:
   - **2454** - Deve ter STATUS="VALIDADO", DOC.SOMA="TRF_295551"
   - **3736** - Deve ter STATUS="VALIDADO", DOC.SOMA="TRF_295552"

---

## ⚠️ Troubleshooting

### ❌ "Transferências não foram processadas"

**Verificar:**
```bash
# 1. Agendador está em execução?
ps aux | grep agendador.py

# 2. Há erros nos logs?
tail -100 logs/soma_direct_*.log | grep ERROR

# 3. Autenticação SOMA funciona?
python -c "from core.auth import SomaAuthenticator; from config.settings import Settings; auth = SomaAuthenticator(Settings.from_env(), None); auth.login() and print('✅ Auth OK')"
```

### ❌ "Erro: DOC. SOMA deve conter..."

**Solução:** O bug de `doc_id` foi corrigido no commit. Se persistir:
```bash
# Verificar se alterações estão no servidor
git diff HEAD~1 workflows/orchestrator.py | grep "doc_id="
```

### ❌ "Timeout na Google Sheets"

**Solução:** Aguardar alguns minutos e tentar novamente. Limite de quota (429).

---

## ✅ Validação Pós-Deployment

Execute esta checklist após o deployment:

- [ ] Alterações foram feitas pull com sucesso (`git pull` mostra fc195a5)
- [ ] Agendador foi reiniciado (novo processo)
- [ ] Logs mostram execução a cada ~60 segundos
- [ ] Primeira verificação: Transferências 2454 e 3736 com STATUS="VALIDADO"
- [ ] Logs não mostram erros relacionados a Transferências
- [ ] Próximas execuções (10 minutos depois) ainda processam corretamente
- [ ] Nenhum novo "EM ERRO" apareceu na planilha

---

## 📈 Próximos Passos

### Se Tudo Funciona ✅
1. Deixar agendador em execução permanente
2. Monitorar logs periodicamente (1-2x por semana)
3. Validar planilha para novos registos processados

### Se Houver Problemas ❌
1. Capturar logs completos (últimas 100 linhas)
2. Verificar alterações no código: `git diff HEAD~1`
3. Contactar para suporte se problema persistir

---

## 📞 Referência Rápida

**Ficheiros Críticos:**
- `agendador.py` - Scheduler principal (executa a cada 60s)
- `workflows/orchestrator.py` - Orquestrador (processa Transferências)
- `services/soma_api_service.py` - Busca de Transferências
- `services/sheets_service.py` - Atualização da planilha

**Variáveis de Ambiente:**
- `AGENDADOR_INTERVAL_SECONDS` - Intervalo do agendador (default: 60)
- `SOMA_INSTITUTION_ID` - ID da instituição (deve ser 270)

---

**Status:** ✅ Pronto para Deployment  
**Teste Local:** ✅ Validado  
**Commit:** fc195a5  
**Data Criação:** 26/09/2026

