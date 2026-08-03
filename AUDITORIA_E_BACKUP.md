# ARK EDUS — auditoria técnica e backup automático

## Implementado nesta entrega

- backup automático com intervalo configurável;
- cópia consistente do SQLite pela API `Connection.backup()`;
- validação `PRAGMA integrity_check` antes da conclusão;
- compactação do banco e dos uploads em ZIP;
- criptografia opcional com chave Fernet;
- retenção automática dos arquivos antigos;
- bloqueio para impedir backups simultâneos;
- registro de sucesso e falha no banco;
- painel exclusivo do Administrador Geral;
- criação e download de backup manual;
- registro de auditoria nos backups manuais e downloads;
- aviso específico para uso de Persistent Disk no Render.

## Configuração recomendada no Render

```env
BACKUP_AUTOMATICO=true
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_DAYS=30
BACKUP_INCLUDE_UPLOADS=true
BACKUP_DIR=/var/data/backups
```

O caminho `/var/data/backups` deve estar dentro de um **Persistent Disk**.
Sem disco persistente, backups salvos apenas no sistema de arquivos do serviço
podem desaparecer após reinicializações ou novos deploys.

Para criptografar cada arquivo de backup, gere uma chave Fernet e configure:

```env
BACKUP_ENCRYPTION_KEY=SUA_CHAVE_FERNET
```

A chave deve ser guardada fora do ZIP e fora do repositório. Sem essa chave,
um arquivo `.zip.fernet` não poderá ser recuperado.

## Como acessar

Entre como **Administrador Geral** e abra **Backups do Sistema** no menu lateral.
O primeiro backup automático é verificado após a inicialização e, depois disso,
o serviço verifica uma vez por hora se o intervalo configurado já foi atingido.
