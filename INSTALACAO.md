# Instalação da ARK EDUS

No GitHub Codespaces, execute uma vez:

```bash
python -m pip install -r requirements.txt
python app.py
```

Ou execute:

```bash
./instalar_e_iniciar.sh
```

O pacote `cryptography` é obrigatório para proteger CPFs. No Render, mantenha o comando de build como `pip install -r requirements.txt`.

## Variáveis obrigatórias em produção

- `SECRET_KEY`
- `LGPD_ENCRYPTION_KEY`
- `LGPD_HASH_KEY`
- `SESSION_COOKIE_SECURE=true`
- `DATABASE_PATH`

Para gerar as chaves:

```bash
python gerar_chaves_lgpd.py
```
