
# ARK EDUS — implementação de Segurança e LGPD

## Entregue nesta versão

- Aceite obrigatório dos Termos de Uso e da Política de Privacidade no primeiro login e sempre que a versão mudar.
- Registro de aceite com usuário, documento, versão, data/hora, IP e navegador.
- Página **Segurança e Privacidade** com sessões, dispositivos, histórico, documentos aceitos e solicitações LGPD.
- Exportação dos próprios dados em JSON.
- Solicitações de acesso, correção, exportação, exclusão/anonimização, revogação e outros direitos.
- Painel LGPD para administradores com solicitações, aceites, logs e incidentes.
- Registro e encerramento de sessões.
- Registro de login, logout, alterações, exportações e solicitações no log de auditoria.
- CPF criptografado no banco, assinatura para duplicidade e exibição mascarada.
- Links legais no login, menu interno e rodapé.
- Cookies, expiração de sessão e cabeçalhos de segurança.

## Variáveis de ambiente

Defina no Render:

```env
SECRET_KEY=uma-chave-aleatoria-longa
LGPD_ENCRYPTION_KEY=chave-fernet-gerada
CPF_HASH_KEY=uma-chave-secreta-para-assinatura
SESSION_COOKIE_SECURE=true
SESSION_TIMEOUT_MINUTES=30
TERMOS_VERSAO=1.0
PRIVACIDADE_VERSAO=1.0
CANAL_PRIVACIDADE=privacidade@arkedus.com.br
```

Quando os documentos legais forem atualizados, altere `TERMOS_VERSAO` ou `PRIVACIDADE_VERSAO`. Todos os usuários precisarão aceitar novamente a nova versão.

## Observação jurídica

Os textos incluídos são uma base operacional e informativa. Antes da comercialização definitiva, devem ser revisados por profissional jurídico, considerando os contratos, fornecedores, hospedagem e fluxo real de dados da ARK EDUS.

## Backup automático

A plataforma gera backups consistentes usando a API nativa de backup do SQLite,
valida a integridade da cópia e compacta o banco juntamente com os uploads.

Variáveis disponíveis:

- `BACKUP_AUTOMATICO=true`
- `BACKUP_INTERVAL_HOURS=24`
- `BACKUP_RETENTION_DAYS=30`
- `BACKUP_INCLUDE_UPLOADS=true`
- `BACKUP_DIR=/var/data/backups` (recomendado no disco persistente do Render)
- `BACKUP_ENCRYPTION_KEY=` (opcional; chave Fernet)

O menu **Backups do Sistema** é exclusivo do Administrador Geral e permite gerar
e baixar cópias manuais. Em serviços Render sem Persistent Disk, os arquivos
locais podem ser perdidos a cada reinicialização ou novo deploy.
