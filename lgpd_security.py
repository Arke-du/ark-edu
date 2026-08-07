"""Recursos centrais de privacidade e segurança da ARK EDUS.

As chaves devem ser definidas no ambiente em produção:
- LGPD_ENCRYPTION_KEY: chave Fernet (44 caracteres base64 url-safe)
- LGPD_HASH_KEY: segredo longo usado no HMAC de pesquisa/duplicidade
"""
from __future__ import annotations

import base64
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


def _segredo_base() -> bytes:
    """Retorna um segredo estável para os recursos LGPD.

    Em produção, prefira LGPD_ENCRYPTION_KEY/LGPD_HASH_KEY. Para não bloquear
    cadastros em ambientes já configurados apenas com SECRET_KEY (ex.:
    Codespaces/Render), usamos SECRET_KEY como fallback seguro e determinístico.
    """
    segredo = (os.environ.get("SECRET_KEY") or "").strip()
    if not segredo:
        # Compatibilidade com o valor padrão usado pela aplicação local.
        segredo = "chave-temporaria-local-altere-no-render"
    return segredo.encode("utf-8")


def _fernet() -> Fernet:
    chave_texto = os.environ.get("LGPD_ENCRYPTION_KEY", "").strip()
    if chave_texto:
        try:
            return Fernet(chave_texto.encode())
        except (ValueError, TypeError):
            # Se a variável foi preenchida incorretamente, não derruba o cadastro:
            # deriva uma chave válida a partir do segredo principal da aplicação.
            pass
    chave = base64.urlsafe_b64encode(hashlib.sha256(b"ARKEDUS-LGPD:" + _segredo_base()).digest())
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
    segredo_configurado = os.environ.get("LGPD_HASH_KEY", "").strip()
    segredo = segredo_configurado.encode() if segredo_configurado else hashlib.sha256(
        b"ARKEDUS-LGPD-HASH:" + _segredo_base()
    ).digest()
    return hmac.new(segredo, numeros.encode(), hashlib.sha256).hexdigest()


def preparar_cpf(cpf: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    numeros = somente_digitos(cpf)
    if not numeros:
        return None, None, None
    return criptografar_cpf(numeros), hash_cpf(numeros), numeros[-2:]
