"""Regras centralizadas de planos e assinaturas da ARK EDUS."""
from __future__ import annotations

import sqlite3
from datetime import datetime, date, timezone
from typing import Any, Optional

RECURSOS_PROFESSOR = {
    "questoes_discursivas",
    "correcao_discursivas",
    "relatorios_completos",
    "exportacao_pdf_sem_marca",
    "multiplos_modelos",
    "embaralhamento",
    "suporte_prioritario",
}

STATUS_LIBERADOS = {"ativa", "gratuita", "trial"}


def _row_value(row: Any, key: str, default=None):
    try:
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


def obter_conta(conectar_banco, usuario_id: int):
    """Retorna a conta SaaS efetiva do usuário.

    Para usuários institucionais, a assinatura/cortesia da instituição tem
    precedência sobre uma assinatura individual pendente. Isso evita cobrar ou
    bloquear professores que já estão cobertos pelo plano da escola.
    """
    db = conectar_banco()
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    try:
        base = cur.execute(
            """
            SELECT
                u.id AS usuario_id,
                u.escola_id,
                COALESCE(u.tipo_conta, 'institucional') AS tipo_conta,
                CASE
                    WHEN COALESCE(u.tipo_conta, 'institucional') != 'autonomo'
                        THEN COALESCE(e.plano_codigo, u.plano_codigo)
                    ELSE COALESCE(u.plano_codigo, e.plano_codigo)
                END AS plano_codigo,
                COALESCE(u.assinatura_status, 'ativa') AS assinatura_status,
                c.nome AS cargo,
                p.nome AS plano_nome,
                p.valor_mensal,
                e.nome_instituicao,
                p.limite_turmas,
                p.limite_alunos,
                p.limite_provas_mes,
                COALESCE(p.marca_dagua, 0) AS marca_dagua,
                COALESCE(p.suporte_prioritario, 0) AS suporte_prioritario
            FROM usuarios u
            LEFT JOIN cargos c ON c.id = u.cargo_id
            LEFT JOIN escolas e ON e.id = u.escola_id
            LEFT JOIN planos_saas p ON p.codigo = CASE
                WHEN COALESCE(u.tipo_conta, 'institucional') != 'autonomo'
                    THEN COALESCE(e.plano_codigo, u.plano_codigo)
                ELSE COALESCE(u.plano_codigo, e.plano_codigo)
            END
            WHERE u.id = ?
            LIMIT 1
            """,
            (usuario_id,),
        ).fetchone()
        if not base:
            return None

        conta = dict(base)
        escola_id = conta.get('escola_id')
        tipo_conta = (conta.get('tipo_conta') or 'institucional').lower()

        assinatura = cur.execute(
            """
            SELECT a.*
            FROM assinaturas_saas a
            WHERE a.usuario_id = ?
               OR (a.escola_id = ? AND ? != 'autonomo')
            ORDER BY
                CASE
                    /* Cortesia institucional vigente: prioridade máxima. */
                    WHEN a.escola_id = ? AND ? != 'autonomo'
                         AND COALESCE(a.isento, 0) = 1
                         AND (a.isencao_inicio IS NULL OR date(a.isencao_inicio) <= date('now','localtime'))
                         AND (a.isencao_fim IS NULL OR date(a.isencao_fim) >= date('now','localtime')) THEN 0
                    /* Depois, eventual cortesia individual vigente. */
                    WHEN a.usuario_id = ?
                         AND COALESCE(a.isento, 0) = 1
                         AND (a.isencao_inicio IS NULL OR date(a.isencao_inicio) <= date('now','localtime'))
                         AND (a.isencao_fim IS NULL OR date(a.isencao_fim) >= date('now','localtime')) THEN 1
                    /* Plano institucional ativo também cobre o professor. */
                    WHEN a.escola_id = ? AND ? != 'autonomo'
                         AND lower(COALESCE(a.status,'')) IN ('ativa','gratuita','trial','isenta') THEN 2
                    WHEN a.usuario_id = ?
                         AND lower(COALESCE(a.status,'')) IN ('ativa','gratuita','trial','isenta') THEN 3
                    /* Se nada estiver ativo, ainda preferimos o registro da escola
                       para contas institucionais, evitando exibir cobrança autônoma. */
                    WHEN a.escola_id = ? AND ? != 'autonomo' THEN 4
                    WHEN a.usuario_id = ? THEN 5
                    ELSE 6
                END,
                a.id DESC
            LIMIT 1
            """,
            (
                usuario_id, escola_id, tipo_conta,
                escola_id, tipo_conta, usuario_id,
                escola_id, tipo_conta, usuario_id,
                escola_id, tipo_conta, usuario_id,
            ),
        ).fetchone()

        defaults = {
            'assinatura_id': None, 'assinatura_usuario_id': None,
            'assinatura_escola_id': None, 'isento': 0,
            'motivo_isencao': None, 'isencao_inicio': None,
            'isencao_fim': None, 'inicio_em': None, 'proxima_cobranca': None,
            'gateway': None, 'metodo_pagamento': None,
            'renovacao_automatica': None, 'observacao_cobranca': None,
            'teste_inicio': None, 'teste_fim': None, 'plano_pos_teste': None,
            'cancelamento_agendado': 0, 'acesso_ate': None,
        }
        conta.update(defaults)
        if assinatura:
            a = dict(assinatura)
            conta.update({
                'assinatura_id': a.get('id'),
                'assinatura_usuario_id': a.get('usuario_id'),
                'assinatura_escola_id': a.get('escola_id'),
                'isento': a.get('isento') or 0,
                'motivo_isencao': a.get('motivo_isencao'),
                'isencao_inicio': a.get('isencao_inicio'),
                'isencao_fim': a.get('isencao_fim'),
                'inicio_em': a.get('inicio_em'),
                'proxima_cobranca': a.get('proxima_cobranca'),
                'gateway': a.get('gateway'),
                'metodo_pagamento': a.get('metodo_pagamento'),
                'renovacao_automatica': a.get('renovacao_automatica'),
                'observacao_cobranca': a.get('observacao_cobranca'),
                'teste_inicio': a.get('teste_inicio'),
                'teste_fim': a.get('teste_fim'),
                'plano_pos_teste': a.get('plano_pos_teste'),
                'cancelamento_agendado': a.get('cancelamento_agendado') or 0,
                'acesso_ate': a.get('acesso_ate'),
                'assinatura_status': a.get('status') or conta.get('assinatura_status') or 'ativa',
            })
        return conta
    finally:
        db.close()


def isencao_vigente(conta) -> bool:
    """Retorna True quando a conta possui cortesia/isenção válida."""
    if not conta or not bool(int(_row_value(conta, "isento", 0) or 0)):
        return False
    inicio = _row_value(conta, "isencao_inicio")
    fim = _row_value(conta, "isencao_fim")
    hoje = date.today()
    try:
        if inicio and date.fromisoformat(str(inicio)[:10]) > hoje:
            return False
        if fim and date.fromisoformat(str(fim)[:10]) < hoje:
            return False
    except ValueError:
        return False
    return True



def teste_vigente(conta) -> bool:
    """Retorna True enquanto o período de teste gratuito estiver válido."""
    if not conta or str(_row_value(conta, "assinatura_status", "")).lower() != "trial":
        return False
    fim = _row_value(conta, "teste_fim")
    if not fim:
        return False
    try:
        fim_dt = datetime.fromisoformat(str(fim).replace("Z", "+00:00"))
        if fim_dt.tzinfo is None:
            fim_dt = fim_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < fim_dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return False


def dias_restantes_teste(conta) -> int:
    fim = _row_value(conta, "teste_fim") if conta else None
    if not fim:
        return 0
    try:
        fim_dt = datetime.fromisoformat(str(fim).replace("Z", "+00:00"))
        if fim_dt.tzinfo is None:
            fim_dt = fim_dt.replace(tzinfo=timezone.utc)
        segundos = (fim_dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        if segundos <= 0:
            return 0
        return max(1, int((segundos + 86399) // 86400))
    except (TypeError, ValueError):
        return 0

def assinatura_ativa(conta) -> bool:
    if not conta:
        return False
    # Uma cortesia vigente ignora cobrança, vencimento e status do gateway.
    if isencao_vigente(conta) or teste_vigente(conta):
        return True
    # Contas institucionais legadas sem plano continuam funcionando.
    if _row_value(conta, "tipo_conta") != "autonomo" and not _row_value(conta, "plano_codigo"):
        return True
    return str(_row_value(conta, "assinatura_status", "ativa")).lower() in STATUS_LIBERADOS


def possui_recurso(conta, recurso: str) -> bool:
    if not conta:
        return False
    tipo = _row_value(conta, "tipo_conta")
    plano = (_row_value(conta, "plano_codigo") or "").lower()
    # Instituições têm todos os recursos funcionais; os planos diferem por alunos e suporte.
    if tipo != "autonomo":
        return True
    if teste_vigente(conta) or plano == "professor":
        return True
    if plano == "gratuito":
        return recurso not in RECURSOS_PROFESSOR
    return False


def motivo_recurso(recurso: str) -> str:
    nomes = {
        "questoes_discursivas": "questões discursivas",
        "correcao_discursivas": "correção de questões discursivas",
        "relatorios_completos": "relatórios completos",
        "exportacao_pdf_sem_marca": "exportação sem marca d'água",
        "multiplos_modelos": "múltiplos modelos de prova",
        "embaralhamento": "embaralhamento de questões e alternativas",
        "suporte_prioritario": "suporte prioritário",
    }
    return nomes.get(recurso, recurso.replace("_", " "))


def contar_turmas(cur, escola_id: int) -> int:
    return cur.execute("SELECT COUNT(*) FROM turmas WHERE escola_id = ?", (escola_id,)).fetchone()[0]


def contar_alunos(cur, escola_id: int) -> int:
    return cur.execute("SELECT COUNT(*) FROM alunos WHERE escola_id = ?", (escola_id,)).fetchone()[0]


def contar_provas_mes(cur, escola_id: int) -> int:
    """Conta avaliações criadas no mês, aceitando datas ISO e DD/MM/AAAA."""
    return cur.execute(
        """
        SELECT COUNT(*) FROM provas
        WHERE escola_id = ?
          AND (
              strftime('%Y-%m', atualizado_em) = strftime('%Y-%m', 'now', 'localtime')
              OR strftime('%Y-%m', data_geracao) = strftime('%Y-%m', 'now', 'localtime')
              OR (
                  length(data_geracao) >= 10
                  AND substr(data_geracao, 7, 4) || '-' || substr(data_geracao, 4, 2)
                      = strftime('%Y-%m', 'now', 'localtime')
              )
          )
        """,
        (escola_id,),
    ).fetchone()[0]


def verificar_limite(conectar_banco, conta, tipo: str) -> tuple[bool, Optional[str]]:
    if not conta:
        return False, "Não foi possível identificar o plano da conta."
    escola_id = _row_value(conta, "escola_id")
    limite_key = {
        "turmas": "limite_turmas",
        "alunos": "limite_alunos",
        "provas_mes": "limite_provas_mes",
    }[tipo]
    limite = _row_value(conta, limite_key)
    if limite is None:
        return True, None
    db = conectar_banco()
    cur = db.cursor()
    try:
        total = {
            "turmas": contar_turmas,
            "alunos": contar_alunos,
            "provas_mes": contar_provas_mes,
        }[tipo](cur, escola_id)
    finally:
        db.close()
    if total < int(limite):
        return True, None
    mensagens = {
        "turmas": f"Seu plano permite até {limite} turma(s). Faça upgrade para continuar.",
        "alunos": f"Seu plano permite até {limite} aluno(s). Faça upgrade para continuar.",
        "provas_mes": f"Seu plano permite até {limite} prova(s) por mês. Faça upgrade para continuar.",
    }
    return False, mensagens[tipo]


def usa_marca_dagua(conta) -> bool:
    return bool(conta and int(_row_value(conta, "marca_dagua", 0) or 0))
