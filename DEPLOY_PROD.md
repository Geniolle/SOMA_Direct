# SOMA Direct — Guia de Produção e Deployment

Referência para o servidor de produção e operação do serviço `somadirect.service`.

## Detalhes do Servidor de Produção

- **Ambiente**: Oracle Cloud Infrastructure (OCI - Londres)
- **Host / IP**: `opc@145.241.202.16` (`servidor-tesouraria-v2`)
- **Chave SSH**: `ssh-key-2026-08-30_v2.key`
- **Diretório do Projeto**: `/home/opc/SOMA_Direct`
- **Ambiente Virtual**: `/home/opc/SOMA_Direct/.venv` (Python 3.11 gerenciado por `uv`)
- **Serviço de Produção**: `systemd` unit `somadirect.service`
- **Comando do Serviço**: `/home/opc/SOMA_Direct/.venv/bin/python /home/opc/SOMA_Direct/agendador.py`
- **Intervalo Padrão**: 60 segundos (`AGENDADOR_INTERVAL_SECONDS=60`)

## Comandos Operacionais no Servidor

### Ver status do serviço
```bash
sudo systemctl status somadirect.service --no-pager
```

### Acompanhar logs em tempo real
```bash
sudo journalctl -u somadirect.service -f
```

### Reiniciar o serviço
```bash
sudo systemctl restart somadirect.service
```

### Parar / Iniciar o serviço
```bash
sudo systemctl stop somadirect.service
sudo systemctl start somadirect.service
```

## Procedimento de Atualização (Deploy de Novos Commits)

```bash
ssh -i ssh-key-2026-08-30_v2.key opc@145.241.202.16
cd /home/opc/SOMA_Direct
git pull origin main
/home/opc/.local/bin/uv pip install -r requirements.txt
sudo systemctl restart somadirect.service
sudo systemctl status somadirect.service --no-pager
```
