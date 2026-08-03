"""Recursos centrais de privacidade e segurança da ARK EDUS.

As chaves devem ser definidas no ambiente em produção:
- LGPD_ENCRYPTION_KEY: chave Fernet (44 caracteres base64 url-safe)
- LGPD_HASH_KEY: segredo longo usado no HMAC de pesquisa/duplicidade
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def somente_digitos(valor: Optional[str]) -> str:
    return re.sub(r"\D", "", valor or "")


def validar_cpf(cpf: Optional[str]) -> bool:
    numeros = somente_digitos(cpf)
    if len(numeros) != 11 or numeros == numeros[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(numeros[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11
        if digito == 10:
            digito = 0
        if digito != int(numeros[tamanho]):
            return False
    return True


def mascarar_cpf(valor: Optional[str]) -> str:
    numeros = somente_digitos(valor)
    if len(numeros) < 2:
        return "Não informado"
    return f"***.***.***-{numeros[-2:]}"


def _fernet() -> Fernet:
    chave = os.environ.get("LGPD_ENCRYPTION_KEY", "").strip().encode()
    if not chave:
        raise RuntimeError("Defina LGPD_ENCRYPTION_KEY no ambiente antes de cadastrar CPF.")
    return Fernet(chave)


def criptografar_cpf(cpf: Optional[str]) -> Optional[str]:
    numeros = somente_digitos(cpf)
    if not numeros:
        return None
    if not validar_cpf(numeros):
        raise ValueError("CPF inválido.")
    return _fernet().encrypt(numeros.encode()).decode()


def descriptografar_cpf(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def hash_cpf(cpf: Optional[str]) -> Optional[str]:
    numeros = somente_digitos(cpf)
    if not numeros:
        return None
    segredo = os.environ.get("LGPD_HASH_KEY", "").encode()
    if not segredo:
        raise RuntimeError("Defina LGPD_HASH_KEY no ambiente antes de cadastrar CPF.")
    return hmac.new(segredo, numeros.encode(), hashlib.sha256).hexdigest()


def preparar_cpf(cpf: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    numeros = somente_digitos(cpf)
    if not numeros:
        return None, None, None
    return criptografar_cpf(numeros), hash_cpf(numeros), numeros[-2:]
