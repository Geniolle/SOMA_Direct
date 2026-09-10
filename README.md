# SOMA Direct — Motor Modular de Alta Performance

Projeto moderno, desacoplado e de alta performance para automação e integração com o sistema **SOMA**.

## Diferencial em Relação ao Projeto Legado

| Recurso | Projeto Legado (Selenium UI) | SOMA Direct (HTTP Session) |
| :--- | :--- | :--- |
| **Tempo Médio por Linha** | 70 a 100 segundos | **1 a 2 segundos (~45x mais rápido)** |
| **Consumo de Recursos** | Instância pesada do Google Chrome | Requisições HTTP ultraleves |
| **Fragilidade de Layout** | Dependente de XPaths, CSS e Modals | Comunica diretamente com endpoints PHP |
| **Obtenção do DOC. SOMA** | Busca reversa por data na listagem | **Resposta JSON direta com o ID** |
| **Modularidade** | Monolítico acoplado a webdriver | Totalmente modular (`core`, `services`, `workflows`) |

## Estrutura

- `config/`: Configurações centralizadas via `.env`.
- `core/`: Sessão HTTP resiliente e autenticador com cookie de sessão.
- `domain/`: Modelos tipados e regras de negócio.
- `services/`: Serviços dedicados para a API SOMA e Google Sheets.
- `workflows/`: Orquestração de lotes e execução individual.
- `main.py`: Ponto de entrada CLI rápido.

## Resultados Reais do Benchmark (3 Registros de Teste)

| Linha / Tipo | Selenium Legado (`SOMA`) | Novo Motor (`SOMA_Direct`) | DOC Gerado | Status Planilha |
| :--- | :--- | :--- | :--- | :--- |
| **4248 (Entrada)** | 100.22s | **2.48s** | `5485185` | `VALIDADO` |
| **4246 (Saída)** | 99.43s *(timeout/fallback)* | **4.31s** | `5485186` | `VALIDADO` |
| **4247 (Transferência)** | 60.42s | **4.04s** | `TRF_291117` | `VALIDADO` |
| **Tempo Total** | **282.76s (~4.7 min)** | **14.26s (21.49s com init)** | **100% Sucesso** | **Planilha Atualizada** |

> **Aceleração**: Redução de tempo de **95%** (**~20x** no lote e até **~40x** por item individual). Zero falhas de layout ou timeouts de interface.

## Execução

```bash
# Executar as linhas padrão ou lote
python main.py

# Ou especificar linhas pontuais
python main.py 4248 4246 4247
```
## Ronda completa por intervalo

Execute sem parâmetros para informar o intervalo e o modo interativamente:

```powershell
.\.venv\Scripts\python.exe .\ronda_completa.py
```

Também é possível executar sem perguntas:

```powershell
.\.venv\Scripts\python.exe .\ronda_completa.py --inicio 01/08/2026 --fim 31/08/2026 --simulation
.\.venv\Scripts\python.exe .\ronda_completa.py --inicio 01/08/2026 --fim 31/08/2026 --apply
```

O modo de simulação é somente leitura. O modo de aplicação grava `Confirmado`
quando todas as validações passam ou a mensagem concreta da divergência em
`AUDITORIA`.
