import base64
import json
import os
import sqlite3
import uuid
import re

from datetime import datetime
from io import BytesIO

import cv2
import qrcode
from PIL import Image
from flask import Flask, flash, redirect, render_template, request, session, jsonify
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(BASE_DIR, "plataforma.db")
)
UPLOAD_FOLDER = os.environ.get(
    "UPLOAD_FOLDER",
    os.path.join(BASE_DIR, "static", "uploads")
)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "chave-temporaria-local-altere-no-render"
)

app.config.update(
    MAIL_SERVER=os.environ.get("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.environ.get("MAIL_PORT", "587")),
    MAIL_USE_TLS=os.environ.get("MAIL_USE_TLS", "true").lower() == "true",
    MAIL_USE_SSL=os.environ.get("MAIL_USE_SSL", "false").lower() == "true",
    MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
    MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER")
    or os.environ.get("MAIL_USERNAME"),
    UPLOAD_FOLDER=UPLOAD_FOLDER,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

mail = Mail(app)
serializer = URLSafeTimedSerializer(app.secret_key)


def conectar_banco():
    return sqlite3.connect(DB_PATH)


def sincronizar_ano_letivo_instituicao(cursor, escola_id, ano, tornar_ativo=True):
    """Cria ou atualiza o ano letivo oficial de uma instituição.

    A tabela anos_letivos é a fonte oficial. O campo escolas.ano_letivo
    permanece sincronizado apenas por compatibilidade com telas antigas.
    """
    if not escola_id or ano in (None, ""):
        return None

    try:
        ano = int(ano)
    except (TypeError, ValueError):
        raise ValueError("O ano letivo informado é inválido.")

    cursor.execute("""
        SELECT id, ativo, encerrado
        FROM anos_letivos
        WHERE escola_id = ?
          AND ano = ?
        LIMIT 1
    """, (escola_id, ano))

    registro = cursor.fetchone()

    if tornar_ativo:
        cursor.execute("""
            UPDATE anos_letivos
            SET ativo = 0
            WHERE escola_id = ?
              AND ativo = 1
        """, (escola_id,))

    if registro:
        cursor.execute("""
            UPDATE anos_letivos
            SET ativo = ?,
                encerrado = 0
            WHERE id = ?
        """, (1 if tornar_ativo else registro["ativo"], registro["id"]))
        ano_letivo_id = registro["id"]
    else:
        cursor.execute("""
            INSERT INTO anos_letivos (
                escola_id,
                ano,
                ativo,
                encerrado
            )
            VALUES (?, ?, ?, 0)
        """, (escola_id, ano, 1 if tornar_ativo else 0))
        ano_letivo_id = cursor.lastrowid

    cursor.execute("""
        UPDATE escolas
        SET ano_letivo = ?
        WHERE id = ?
    """, (str(ano), escola_id))

    return ano_letivo_id


def sincronizar_anos_letivos_legados():
    """Migra automaticamente instituições antigas que só têm escolas.ano_letivo."""
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cursor.execute("""
            SELECT id, ano_letivo
            FROM escolas
            WHERE TRIM(COALESCE(ano_letivo, '')) <> ''
        """)

        for escola in cursor.fetchall():
            cursor.execute("""
                SELECT id
                FROM anos_letivos
                WHERE escola_id = ?
                  AND ativo = 1
                  AND encerrado = 0
                LIMIT 1
            """, (escola["id"],))

            if cursor.fetchone() is None:
                sincronizar_ano_letivo_instituicao(
                    cursor,
                    escola["id"],
                    escola["ano_letivo"],
                    tornar_ativo=True
                )

        banco.commit()

    except (sqlite3.Error, ValueError) as erro:
        banco.rollback()
        print("ERRO AO SINCRONIZAR ANOS LETIVOS LEGADOS:", erro)

    finally:
        banco.close()

def obter_escola_usuario(usuario_id=None):
    """
    Retorna o ID da instituição vinculada ao usuário.

    Primeiro tenta utilizar a escola registrada na sessão.
    Se não existir, consulta a tabela usuarios e atualiza
    a sessão automaticamente.
    """

    escola_id = session.get("escola_id")

    if escola_id:
        try:
            return int(escola_id)
        except (TypeError, ValueError):
            session.pop("escola_id", None)

    if not usuario_id:
        usuario_id = session.get("usuario_id")

    if not usuario_id:
        return None

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        cursor.execute("""
            SELECT escola_id
            FROM usuarios
            WHERE id = ?
            LIMIT 1
        """, (
            usuario_id,
        ))

        usuario = cursor.fetchone()

        if not usuario or not usuario["escola_id"]:
            return None

        escola_id = int(usuario["escola_id"])

        session["escola_id"] = escola_id

        return escola_id

    except sqlite3.Error as erro:

        print(
            "ERRO AO RECUPERAR A INSTITUIÇÃO DO USUÁRIO:",
            erro
        )

        return None

    finally:
        banco.close()


def obter_ano_letivo_ativo(escola_id):
    """
    Retorna o ano letivo oficialmente ativo da instituição.

    Retorno:
        sqlite3.Row com:
        - id
        - escola_id
        - ano
        - data_inicio
        - data_fim
        - ativo
        - encerrado

    Retorna None quando a instituição não possui ano ativo.
    """

    if not escola_id:
        return None

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        cursor.execute("""
            SELECT
                id,
                escola_id,
                ano,
                data_inicio,
                data_fim,
                ativo,
                encerrado
            FROM anos_letivos
            WHERE escola_id = ?
              AND ativo = 1
              AND encerrado = 0
            ORDER BY ano DESC
            LIMIT 1
        """, (
            escola_id,
        ))

        return cursor.fetchone()

    except sqlite3.Error as erro:

        print(
            "ERRO AO BUSCAR O ANO LETIVO ATIVO:",
            erro
        )

        return None

    finally:
        banco.close()


def obter_ano_letivo_selecionado(escola_id):
    """
    Retorna o ano letivo que deve ser utilizado pela plataforma.

    Futuramente, o administrador poderá selecionar um ano antigo
    para consulta. Quando não houver seleção manual, será utilizado
    automaticamente o ano letivo ativo.
    """

    if not escola_id:
        return None

    ano_selecionado_id = session.get("ano_letivo_selecionado_id")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        # =====================================================
        # ANO ESCOLHIDO MANUALMENTE PARA CONSULTA
        # =====================================================

        if ano_selecionado_id:

            cursor.execute("""
                SELECT
                    id,
                    escola_id,
                    ano,
                    data_inicio,
                    data_fim,
                    ativo,
                    encerrado
                FROM anos_letivos
                WHERE id = ?
                  AND escola_id = ?
                LIMIT 1
            """, (
                ano_selecionado_id,
                escola_id
            ))

            ano_selecionado = cursor.fetchone()

            if ano_selecionado:
                return ano_selecionado

            # Remove da sessão caso o ano não pertença à escola.
            session.pop(
                "ano_letivo_selecionado_id",
                None
            )

        # =====================================================
        # ANO ATIVO PADRÃO
        # =====================================================

        cursor.execute("""
            SELECT
                id,
                escola_id,
                ano,
                data_inicio,
                data_fim,
                ativo,
                encerrado
            FROM anos_letivos
            WHERE escola_id = ?
              AND ativo = 1
              AND encerrado = 0
            ORDER BY ano DESC
            LIMIT 1
        """, (
            escola_id,
        ))

        return cursor.fetchone()

    except sqlite3.Error as erro:

        print(
            "ERRO AO BUSCAR O ANO LETIVO SELECIONADO:",
            erro
        )

        return None

    finally:
        banco.close()


def atualizar_ano_letivo_na_sessao(escola_id):
    """
    Atualiza a sessão com o ano letivo utilizado pela plataforma.

    Retorna o registro do ano letivo ou None.
    """

    ano_letivo = obter_ano_letivo_selecionado(
        escola_id
    )

    if not ano_letivo:

        session.pop("ano_letivo_id", None)
        session.pop("ano_letivo", None)

        return None

    session["ano_letivo_id"] = ano_letivo["id"]
    session["ano_letivo"] = ano_letivo["ano"]

    return ano_letivo


# =========================================================
# GERENCIADOR GLOBAL DO ANO LETIVO
# =========================================================

def garantir_ano_atual_para_escola(escola_id):
    """
    Garante que o ano civil atual exista na tabela anos_letivos.

    A criação automática NÃO encerra nem troca o ano ativo existente.
    Quando a instituição ainda não possui nenhum ano letivo, o ano
    atual é criado como ativo. Caso já exista outro ano ativo, o novo
    ano é criado apenas como preparado, aguardando migração/ativação.
    """

    if not escola_id:
        return None

    ano_atual = datetime.now().year

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cursor.execute("""
            SELECT id, escola_id, ano, ativo, encerrado
            FROM anos_letivos
            WHERE escola_id = ? AND ano = ?
            LIMIT 1
        """, (escola_id, ano_atual))

        existente = cursor.fetchone()
        if existente:
            return existente

        cursor.execute("""
            SELECT id
            FROM anos_letivos
            WHERE escola_id = ?
              AND ativo = 1
              AND encerrado = 0
            LIMIT 1
        """, (escola_id,))

        possui_ativo = cursor.fetchone() is not None

        cursor.execute("""
            INSERT INTO anos_letivos (
                escola_id,
                ano,
                ativo,
                encerrado
            )
            VALUES (?, ?, ?, 0)
        """, (
            escola_id,
            ano_atual,
            0 if possui_ativo else 1
        ))

        novo_id = cursor.lastrowid
        banco.commit()

        cursor.execute("""
            SELECT id, escola_id, ano, ativo, encerrado
            FROM anos_letivos
            WHERE id = ?
        """, (novo_id,))

        return cursor.fetchone()

    except sqlite3.Error as erro:
        banco.rollback()
        print("ERRO AO GARANTIR ANO ATUAL:", erro)
        return None

    finally:
        banco.close()


def obter_ano_global_administrador():
    """Retorna o número do ano visualizado pelo Administrador Geral."""

    ano_sessao = session.get("ano_letivo_visualizado")

    if ano_sessao:
        try:
            return int(ano_sessao)
        except (TypeError, ValueError):
            session.pop("ano_letivo_visualizado", None)

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        ano_atual = datetime.now().year

        cursor.execute("""
            SELECT ano
            FROM anos_letivos
            WHERE ano = ?
            LIMIT 1
        """, (ano_atual,))

        registro = cursor.fetchone()

        if not registro:
            cursor.execute("""
                SELECT ano
                FROM anos_letivos
                WHERE ativo = 1
                  AND encerrado = 0
                ORDER BY ano DESC
                LIMIT 1
            """)
            registro = cursor.fetchone()

        if not registro:
            cursor.execute("""
                SELECT ano
                FROM anos_letivos
                ORDER BY ano DESC
                LIMIT 1
            """)
            registro = cursor.fetchone()

        if not registro:
            return None

        ano = int(registro["ano"])
        session["ano_letivo_visualizado"] = ano
        session["ano_letivo"] = ano
        return ano

    except sqlite3.Error as erro:
        print("ERRO AO OBTER ANO GLOBAL DO ADMINISTRADOR:", erro)
        return None

    finally:
        banco.close()


def obter_contexto_plataforma():
    """
    Retorna o contexto único usado pelas páginas da plataforma.

    Para usuários de uma instituição, ano_letivo_id identifica o
    registro exato da escola. Para o Administrador Geral, o filtro
    global usa o número do ano, porque cada escola possui seu próprio
    ID para o mesmo período.
    """

    usuario_id = session.get("usuario_id")
    cargo = session.get("usuario_cargo", "").strip()
    escola_id = obter_escola_usuario() if cargo != "Administrador Geral" else None

    contexto = {
        "usuario_id": usuario_id,
        "cargo": cargo,
        "escola_id": escola_id,
        "ano_letivo_id": None,
        "ano": None,
        "ano_ativo": None,
        "ano_ativo_id": None,
        "consultando_historico": False
    }

    if not usuario_id:
        return contexto

    if cargo == "Administrador Geral":
        contexto["ano"] = obter_ano_global_administrador()
        return contexto

    if not escola_id:
        return contexto

    garantir_ano_atual_para_escola(escola_id)

    ano_ativo = obter_ano_letivo_ativo(escola_id)
    ano_visualizado = atualizar_ano_letivo_na_sessao(escola_id)

    if ano_ativo:
        contexto["ano_ativo"] = ano_ativo["ano"]
        contexto["ano_ativo_id"] = ano_ativo["id"]

    if ano_visualizado:
        contexto["ano"] = ano_visualizado["ano"]
        contexto["ano_letivo_id"] = ano_visualizado["id"]
        contexto["consultando_historico"] = not (
            ano_visualizado["ativo"] == 1
            and ano_visualizado["encerrado"] == 0
        )

    return contexto


@app.context_processor
def contexto_global_ano_letivo():
    """Disponibiliza o seletor de ano letivo em todos os templates."""

    if "usuario_id" not in session:
        return {
            "ano_contexto": None,
            "ano_ativo_contexto": None,
            "anos_disponiveis": [],
            "consultando_ano_antigo": False
        }

    contexto = obter_contexto_plataforma()
    cargo = contexto["cargo"]

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if cargo == "Administrador Geral":
            cursor.execute("""
                SELECT DISTINCT ano
                FROM anos_letivos
                ORDER BY ano DESC
            """)
        elif contexto["escola_id"]:
            cursor.execute("""
                SELECT id, ano, ativo, encerrado
                FROM anos_letivos
                WHERE escola_id = ?
                ORDER BY ano DESC
            """, (contexto["escola_id"],))
        else:
            return {
                "ano_contexto": None,
                "ano_ativo_contexto": None,
                "anos_disponiveis": [],
                "consultando_ano_antigo": False
            }

        return {
            "ano_contexto": contexto["ano"],
            "ano_ativo_contexto": contexto["ano_ativo"],
            "anos_disponiveis": cursor.fetchall(),
            "consultando_ano_antigo": contexto["consultando_historico"]
        }

    except sqlite3.Error as erro:
        print("ERRO NO CONTEXTO GLOBAL DO ANO LETIVO:", erro)
        return {
            "ano_contexto": contexto.get("ano"),
            "ano_ativo_contexto": contexto.get("ano_ativo"),
            "anos_disponiveis": [],
            "consultando_ano_antigo": False
        }

    finally:
        banco.close()


@app.route("/trocar-ano-letivo/<int:ano>", methods=["POST"])
def trocar_ano_letivo(ano):
    """Troca apenas o ano visualizado, sem alterar o ano ativo."""

    if "usuario_id" not in session:
        return redirect("/login")

    cargo = session.get("usuario_cargo", "").strip()
    escola_id = obter_escola_usuario()

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if cargo == "Administrador Geral":
            cursor.execute("""
                SELECT ano
                FROM anos_letivos
                WHERE ano = ?
                LIMIT 1
            """, (ano,))

            registro = cursor.fetchone()

            if not registro:
                flash("Ano letivo não encontrado.", "erro")
                return redirect(request.referrer or "/")

            session["ano_letivo_visualizado"] = ano
            session["ano_letivo"] = ano
            session.pop("ano_letivo_id", None)
            session.pop("ano_letivo_selecionado_id", None)

        else:
            if not escola_id:
                flash("Não foi possível identificar sua instituição.", "erro")
                return redirect(request.referrer or "/")

            cursor.execute("""
                SELECT id, ano, ativo, encerrado
                FROM anos_letivos
                WHERE escola_id = ?
                  AND ano = ?
                LIMIT 1
            """, (escola_id, ano))

            registro = cursor.fetchone()

            if not registro:
                flash(
                    "O ano letivo selecionado não pertence à sua instituição.",
                    "erro"
                )
                return redirect(request.referrer or "/")

            session["ano_letivo_selecionado_id"] = registro["id"]
            session["ano_letivo_id"] = registro["id"]
            session["ano_letivo"] = registro["ano"]
            session["ano_letivo_visualizado"] = registro["ano"]

        flash(f"Visualizando o ano letivo {ano}.", "success")
        return redirect(request.referrer or "/")

    except sqlite3.Error as erro:
        print("ERRO AO TROCAR ANO LETIVO:", erro)
        flash(f"Erro ao trocar o ano letivo: {erro}", "erro")
        return redirect(request.referrer or "/")

    finally:
        banco.close()


def cargo_permitido(cargos_permitidos):

    if "usuario_id" not in session:
        return False

    return session.get("usuario_cargo") in cargos_permitidos

def criar_tabelas():
    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("PRAGMA foreign_keys = ON")

    def garantir_coluna(tabela, coluna, definicao):
        cursor.execute(f"PRAGMA table_info({tabela})")
        colunas = {linha[1] for linha in cursor.fetchall()}

        if coluna not in colunas:
            cursor.execute(
                f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}"
            )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS escolas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_instituicao TEXT NOT NULL,
            codigo_inep TEXT,
            cnpj TEXT,
            cep TEXT,
            endereco TEXT,
            cidade TEXT,
            estado TEXT,
            telefone TEXT,
            whatsapp TEXT,
            email TEXT,
            site TEXT,
            diretor TEXT,
            coordenador1 TEXT,
            coordenador2 TEXT,
            coordenador3 TEXT,
            secretario TEXT,
            tipo_instituicao TEXT,
            ano_letivo TEXT,
            modalidade_ensino TEXT,
            etapas_ensino TEXT,
            logo TEXT,
            status INTEGER DEFAULT 1,
            criado_em TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS anos_letivos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            escola_id INTEGER NOT NULL,
            ano INTEGER NOT NULL,
            data_inicio TEXT,
            data_fim TEXT,
            ativo INTEGER NOT NULL DEFAULT 0,
            encerrado INTEGER NOT NULL DEFAULT 0,
            criado_em TEXT,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE,
            UNIQUE (escola_id, ano)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cargos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            cargo_id INTEGER,
            ativo INTEGER DEFAULT 1,
            escola_id INTEGER,
            cpf TEXT,
            FOREIGN KEY (cargo_id) REFERENCES cargos(id),
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE SET NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            etapa TEXT,
            ano TEXT NOT NULL,
            turno TEXT NOT NULL,
            escola_id INTEGER,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alunos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            matricula TEXT,
            turma_id INTEGER NOT NULL,
            escola_id INTEGER,
            FOREIGN KEY (turma_id) REFERENCES turmas(id) ON DELETE CASCADE,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aluno_matriculas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aluno_id INTEGER NOT NULL,
            escola_id INTEGER NOT NULL,
            ano_letivo_id INTEGER NOT NULL,
            turma_id INTEGER NOT NULL,
            situacao TEXT NOT NULL DEFAULT 'Cursando',
            data_matricula TEXT DEFAULT CURRENT_TIMESTAMP,
            data_encerramento TEXT,
            observacao TEXT,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id) ON DELETE CASCADE,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE,
            FOREIGN KEY (ano_letivo_id) REFERENCES anos_letivos(id) ON DELETE CASCADE,
            FOREIGN KEY (turma_id) REFERENCES turmas(id) ON DELETE CASCADE,
            UNIQUE (aluno_id, ano_letivo_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT,
            disciplina TEXT,
            escola_id INTEGER,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professor_disciplinas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id INTEGER NOT NULL,
            disciplina TEXT NOT NULL,
            FOREIGN KEY (professor_id) REFERENCES professores(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professor_turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id INTEGER NOT NULL,
            turma_id INTEGER NOT NULL,
            FOREIGN KEY (professor_id) REFERENCES professores(id) ON DELETE CASCADE,
            FOREIGN KEY (turma_id) REFERENCES turmas(id) ON DELETE CASCADE,
            UNIQUE (professor_id, turma_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS componentes_curriculares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            escola_id INTEGER NOT NULL,
            etapa_ensino TEXT NOT NULL,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'padrao',
            ativo INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professor_componentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id INTEGER NOT NULL,
            turma_id INTEGER NOT NULL,
            componente_id INTEGER NOT NULL,
            escola_id INTEGER NOT NULL,
            FOREIGN KEY (professor_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            FOREIGN KEY (turma_id) REFERENCES turmas(id) ON DELETE CASCADE,
            FOREIGN KEY (componente_id) REFERENCES componentes_curriculares(id) ON DELETE CASCADE,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE,
            UNIQUE (professor_id, turma_id, componente_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assuntos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            escola_id INTEGER,
            disciplina TEXT NOT NULL,
            etapa_ensino TEXT NOT NULL,
            ano_serie TEXT NOT NULL,
            nome TEXT NOT NULL,
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_assuntos_filtro
        ON assuntos (
            escola_id,
            disciplina,
            etapa_ensino,
            ano_serie,
            ativo
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disciplina TEXT NOT NULL,
            assunto TEXT,
            tipo_questao TEXT NOT NULL DEFAULT 'multipla_escolha',
            enunciado TEXT NOT NULL,
            imagem TEXT,
            alternativa_a TEXT NOT NULL DEFAULT '',
            alternativa_b TEXT NOT NULL DEFAULT '',
            alternativa_c TEXT NOT NULL DEFAULT '',
            alternativa_d TEXT NOT NULL DEFAULT '',
            correta TEXT NOT NULL DEFAULT '',
            alternativas_json TEXT,
            respostas_corretas TEXT,
            resposta_esperada TEXT,
            criterios_correcao TEXT,
            habilidade TEXT,
            dificuldade TEXT NOT NULL,
            observacoes TEXT,
            escola_id INTEGER,
            criado_por INTEGER,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TEXT,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE,
            FOREIGN KEY (criado_por) REFERENCES usuarios(id) ON DELETE SET NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            turma_id INTEGER NOT NULL,
            professor_id INTEGER,
            disciplina TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            data_geracao TEXT,
            data_aplicacao TEXT,
            escola_id INTEGER,
            FOREIGN KEY (turma_id) REFERENCES turmas(id),
            FOREIGN KEY (professor_id) REFERENCES professores(id),
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prova_questoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prova_id INTEGER NOT NULL,
            questao_id INTEGER NOT NULL,
            FOREIGN KEY (prova_id) REFERENCES provas(id) ON DELETE CASCADE,
            FOREIGN KEY (questao_id) REFERENCES questoes(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS respostas_alunos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prova_id INTEGER,
            aluno_id INTEGER,
            numero_questao INTEGER,
            resposta_aluno TEXT,
            resposta_correta TEXT,
            acertou INTEGER,
            FOREIGN KEY (prova_id) REFERENCES provas(id) ON DELETE CASCADE,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS resultados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prova_id INTEGER,
            aluno_id INTEGER,
            acertos INTEGER,
            erros INTEGER,
            nota REAL,
            FOREIGN KEY (prova_id) REFERENCES provas(id) ON DELETE CASCADE,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS instituicao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            cidade TEXT,
            estado TEXT,
            diretor TEXT,
            coordenador TEXT,
            ano_letivo TEXT,
            logo TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS permissoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cargo_id INTEGER,
            modulo TEXT,
            visualizar INTEGER DEFAULT 0,
            cadastrar INTEGER DEFAULT 0,
            editar INTEGER DEFAULT 0,
            excluir INTEGER DEFAULT 0,
            pode_acessar INTEGER DEFAULT 0,
            FOREIGN KEY (cargo_id) REFERENCES cargos(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS codigos_recuperacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            codigo TEXT NOT NULL,
            usado INTEGER DEFAULT 0,
            criado_em TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
        )
    """)

    # Catálogo pedagógico inicial. Os registros com escola_id NULL
    # ficam disponíveis para todas as instituições. Assuntos temporários
    # digitados durante a criação de uma questão não entram nesta tabela.
    assuntos_padrao = {
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Leitura e interpretação de texto", "Gêneros textuais",
            "Substantivos", "Adjetivos", "Artigos e numerais",
            "Pronomes", "Verbos", "Ortografia", "Pontuação",
            "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Leitura e interpretação de texto", "Gêneros textuais",
            "Verbos e locuções verbais", "Advérbios", "Preposições",
            "Conjunções", "Sujeito e predicado", "Ortografia",
            "Pontuação", "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Leitura e interpretação de texto", "Gêneros textuais",
            "Termos essenciais da oração", "Complementos verbais",
            "Adjunto adnominal e adjunto adverbial", "Vozes verbais",
            "Período simples e composto", "Figuras de linguagem",
            "Pontuação", "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Leitura e interpretação de texto", "Gêneros textuais",
            "Orações coordenadas", "Orações subordinadas",
            "Concordância verbal e nominal", "Regência verbal e nominal",
            "Crase", "Figuras de linguagem", "Variação linguística",
            "Produção textual"
        ],
        ("Matemática", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Números naturais", "Operações fundamentais", "Múltiplos e divisores",
            "Frações", "Números decimais", "Porcentagem", "Razão e proporção",
            "Regra de três", "Geometria plana", "Grandezas e medidas"
        ],
        ("Matemática", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Números inteiros", "Números racionais", "Expressões algébricas",
            "Equações do primeiro grau", "Razão e proporção", "Regra de três",
            "Porcentagem", "Ângulos", "Triângulos", "Probabilidade"
        ],
        ("Matemática", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Produtos notáveis", "Fatoração", "Sistemas de equações",
            "Potenciação e radiciação", "Porcentagem e juros",
            "Teorema de Pitágoras", "Polígonos", "Área e volume",
            "Estatística", "Probabilidade"
        ],
        ("Matemática", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Números reais", "Equações do segundo grau", "Funções",
            "Semelhança de triângulos", "Teorema de Tales",
            "Trigonometria no triângulo retângulo", "Circunferência",
            "Área e volume", "Estatística", "Probabilidade"
        ],
        ("História", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Introdução aos estudos históricos", "Pré-História",
            "Mesopotâmia", "Egito Antigo", "Povos Hebreus",
            "Fenícios e Persas", "Grécia Antiga", "Roma Antiga",
            "África Antiga", "Povos originários da América"
        ],
        ("História", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Feudalismo", "Mundo islâmico", "Renascimento cultural",
            "Reformas religiosas", "Expansão marítima europeia",
            "Povos pré-colombianos", "Colonização espanhola",
            "Colonização portuguesa", "Brasil Colonial",
            "Escravidão e resistência"
        ],
        ("História", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Iluminismo", "Revolução Industrial", "Independência dos Estados Unidos",
            "Revolução Francesa", "Era Napoleônica", "Independências na América",
            "Primeiro Reinado", "Período Regencial", "Segundo Reinado",
            "Abolição da escravidão"
        ],
        ("História", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Primeira República", "Primeira Guerra Mundial",
            "Revolução Russa", "Crise de 1929", "Nazifascismo",
            "Era Vargas", "Segunda Guerra Mundial", "Guerra Fria",
            "Ditadura Militar no Brasil", "Nova República"
        ],
        ("Geografia", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Paisagem e espaço geográfico", "Cartografia",
            "Orientação e coordenadas geográficas", "Relevo",
            "Clima", "Hidrografia", "Vegetação", "População",
            "Espaço urbano e rural", "Questões ambientais"
        ],
        ("Ciências", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Matéria e energia", "Misturas e separação de materiais",
            "Transformações químicas", "Célula", "Tecidos e órgãos",
            "Sistema locomotor", "Sistema nervoso", "Visão e audição",
            "Terra e Universo", "Camadas da Terra"
        ],
        ("Língua Portuguesa", "Ensino Médio", "1ª série"): [
            "Leitura e interpretação", "Gêneros discursivos",
            "Literatura medieval", "Renascimento e Classicismo",
            "Barroco", "Arcadismo", "Funções da linguagem",
            "Variação linguística", "Morfologia", "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Médio", "2ª série"): [
            "Leitura e interpretação", "Romantismo", "Realismo",
            "Naturalismo", "Parnasianismo", "Simbolismo",
            "Sintaxe do período simples", "Sintaxe do período composto",
            "Semântica", "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Médio", "3ª série"): [
            "Leitura e interpretação", "Pré-Modernismo", "Modernismo",
            "Literatura contemporânea", "Concordância", "Regência",
            "Crase", "Coesão e coerência", "Redação do ENEM",
            "Revisão gramatical"
        ]
    }

    for (disciplina_assunto, etapa_assunto, serie_assunto), nomes_assuntos in assuntos_padrao.items():
        for nome_assunto in nomes_assuntos:
            cursor.execute("""
                INSERT INTO assuntos (
                    escola_id,
                    disciplina,
                    etapa_ensino,
                    ano_serie,
                    nome,
                    ativo
                )
                SELECT NULL, ?, ?, ?, ?, 1
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM assuntos
                    WHERE escola_id IS NULL
                      AND disciplina = ?
                      AND etapa_ensino = ?
                      AND ano_serie = ?
                      AND nome = ?
                )
            """, (
                disciplina_assunto, etapa_assunto, serie_assunto, nome_assunto,
                disciplina_assunto, etapa_assunto, serie_assunto, nome_assunto
            ))

    # Catálogo ampliado para o Ensino Fundamental - Anos Finais e Ensino Médio.
    # Apenas assuntos ainda inexistentes são incluídos.
    assuntos_ampliados = {
        # =====================================================
        # ENSINO FUNDAMENTAL - ANOS FINAIS
        # =====================================================
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Tipos de linguagem", "Elementos da comunicação", "Sentido literal e figurado",
            "Sinônimos e antônimos", "Parônimos e homônimos", "Estrutura das palavras",
            "Formação de palavras", "Sílaba tônica", "Acentuação gráfica",
            "Frase, oração e período", "Tipos de frase", "Discurso direto e indireto",
            "Coesão textual", "Coerência textual", "Texto narrativo", "Texto descritivo",
            "Texto injuntivo", "Notícia", "Conto", "Fábula", "Poema",
            "História em quadrinhos", "Bilhete", "Carta pessoal"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Derivação e composição", "Modo indicativo", "Modo subjuntivo",
            "Modo imperativo", "Verbos regulares e irregulares", "Transitividade verbal",
            "Predicação verbal", "Tipos de sujeito", "Tipos de predicado",
            "Complemento nominal", "Aposto e vocativo", "Adjunto adnominal",
            "Adjunto adverbial", "Concordância verbal", "Concordância nominal",
            "Crônica", "Entrevista", "Reportagem", "Carta do leitor",
            "Resumo", "Relato pessoal", "Biografia", "Autobiografia"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Predicativo do sujeito", "Predicativo do objeto", "Objeto direto",
            "Objeto indireto", "Agente da passiva", "Voz ativa", "Voz passiva",
            "Voz reflexiva", "Orações coordenadas", "Orações subordinadas",
            "Conjunções coordenativas", "Conjunções subordinativas",
            "Regência verbal", "Regência nominal", "Crase",
            "Artigo de opinião", "Editorial", "Resenha crítica",
            "Texto publicitário", "Charge", "Cartum", "Infográfico"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Orações subordinadas substantivas", "Orações subordinadas adjetivas",
            "Orações subordinadas adverbiais", "Colocação pronominal",
            "Próclise", "Ênclise", "Mesóclise", "Pontuação no período composto",
            "Denotação e conotação", "Ambiguidade", "Intertextualidade",
            "Ironia", "Humor", "Tese e argumentação", "Estratégias argumentativas",
            "Dissertação argumentativa", "Manifesto", "Debate", "Seminário",
            "Podcast", "Editorial", "Carta aberta"
        ],

        ("Matemática", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Sistema de numeração decimal", "Expressões numéricas",
            "Critérios de divisibilidade", "Números primos", "MMC", "MDC",
            "Frações equivalentes", "Comparação de frações",
            "Adição e subtração de frações", "Multiplicação e divisão de frações",
            "Operações com números decimais", "Plano cartesiano", "Ângulos",
            "Retas e segmentos", "Polígonos", "Perímetro", "Área de figuras planas",
            "Sistema métrico decimal", "Leitura de tabelas", "Leitura de gráficos"
        ],
        ("Matemática", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Operações com números inteiros", "Expressões com números inteiros",
            "Operações com números racionais", "Proporcionalidade direta",
            "Proporcionalidade inversa", "Escala", "Equações com uma incógnita",
            "Inequações", "Sequências numéricas", "Linguagem algébrica",
            "Retas paralelas e transversais", "Congruência de triângulos",
            "Quadriláteros", "Circunferência", "Área de polígonos",
            "Volume de blocos retangulares", "Média aritmética",
            "Gráficos estatísticos", "Princípio multiplicativo"
        ],
        ("Matemática", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Monômios", "Polinômios", "Operações com polinômios",
            "Equações fracionárias", "Sistemas lineares", "Função afim",
            "Sequências", "Notação científica", "Radicais", "Razões notáveis",
            "Semelhança de figuras", "Construções geométricas",
            "Mediatriz e bissetriz", "Prismas", "Cilindros",
            "Área total", "Volume", "Medidas de tendência central",
            "Gráficos e tabelas", "Probabilidade experimental"
        ],
        ("Matemática", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Potências e raízes", "Notação científica", "Produtos notáveis",
            "Fatoração algébrica", "Equações biquadradas", "Sistemas quadráticos",
            "Função afim", "Função quadrática", "Gráficos de funções",
            "Razões trigonométricas", "Relações métricas no triângulo retângulo",
            "Polígonos regulares", "Comprimento da circunferência",
            "Área do círculo", "Prismas e cilindros", "Volume de sólidos",
            "Análise combinatória", "Probabilidade composta",
            "Média, moda e mediana", "Gráficos estatísticos"
        ],

        ("História", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Fontes históricas", "Tempo histórico", "Periodização da História",
            "Evolução humana", "Paleolítico", "Neolítico", "Idade dos Metais",
            "Revolução Agrícola", "Civilizações hidráulicas", "Código de Hamurábi",
            "Religião no Egito", "Sociedade egípcia", "Democracia ateniense",
            "Esparta", "Guerras Médicas", "Guerra do Peloponeso",
            "República Romana", "Império Romano", "Cristianismo",
            "Queda do Império Romano", "Reinos africanos antigos",
            "Povos indígenas do Brasil"
        ],
        ("História", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Império Bizantino", "Império Carolíngio", "Sociedade feudal",
            "Cruzadas", "Renascimento comercial e urbano", "Peste Negra",
            "Formação das monarquias nacionais", "Mercantilismo", "Humanismo",
            "Contrarreforma", "Grandes Navegações", "Conquista da América",
            "Astecas", "Maias", "Incas", "Capitanias hereditárias",
            "Governo-geral", "Economia açucareira", "União Ibérica",
            "Invasões holandesas", "Quilombos"
        ],
        ("História", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Liberalismo", "Despotismo esclarecido", "Independência do Haiti",
            "Congresso de Viena", "Revoluções liberais", "Nacionalismo",
            "Unificação italiana", "Unificação alemã", "Imperialismo",
            "Partilha da África", "Vinda da família real", "Independência do Brasil",
            "Constituição de 1824", "Confederação do Equador",
            "Revoltas Regenciais", "Golpe da Maioridade", "Guerra do Paraguai",
            "Café e industrialização", "Movimento abolicionista",
            "Proclamação da República"
        ],
        ("História", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "República da Espada", "República Oligárquica", "Coronelismo",
            "Revolta da Vacina", "Guerra de Canudos", "Guerra do Contestado",
            "Revolução de 1930", "Estado Novo", "Holocausto",
            "Descolonização da África", "Descolonização da Ásia",
            "Revolução Chinesa", "Revolução Cubana", "Populismo no Brasil",
            "Golpe de 1964", "Regime Militar", "Redemocratização",
            "Constituição de 1988", "Globalização", "Conflitos contemporâneos"
        ],

        ("Geografia", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Lugar, território e região", "Escalas cartográficas", "Fusos horários",
            "Movimentos da Terra", "Estações do ano", "Estrutura interna da Terra",
            "Tectonismo", "Vulcanismo", "Abalos sísmicos", "Tipos de rocha",
            "Formação do solo", "Agentes do relevo", "Elementos do clima",
            "Fatores climáticos", "Bacias hidrográficas", "Biomas brasileiros",
            "Recursos naturais", "Impactos ambientais", "Urbanização",
            "Atividades econômicas"
        ],
        ("Geografia", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Formação territorial do Brasil", "Regiões brasileiras",
            "Regionalização do IBGE", "População brasileira", "Migrações internas",
            "Urbanização brasileira", "Industrialização brasileira",
            "Agropecuária", "Extrativismo", "Fontes de energia",
            "Transportes e comunicação", "Região Norte", "Região Nordeste",
            "Região Centro-Oeste", "Região Sudeste", "Região Sul",
            "Amazônia", "Cerrado", "Semiárido", "Desigualdades regionais"
        ],
        ("Geografia", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "População mundial", "Indicadores demográficos", "Fluxos migratórios",
            "Globalização", "Blocos econômicos", "América Anglo-Saxônica",
            "América Latina", "Estados Unidos", "Canadá", "México",
            "América Central", "América do Sul", "África",
            "Colonização da África", "Economia africana", "Conflitos africanos",
            "Ásia Ocidental", "Oriente Médio", "Geopolítica mundial",
            "Redes e fluxos"
        ],
        ("Geografia", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Europa", "União Europeia", "Rússia", "Ásia", "China", "Japão",
            "Índia", "Tigres Asiáticos", "Oceania", "Austrália",
            "Nova Zelândia", "Antártida", "Nova ordem mundial",
            "Organizações internacionais", "Conflitos contemporâneos",
            "Questões ambientais globais", "Mudanças climáticas",
            "Economia global", "Revolução técnico-científica",
            "Desigualdades socioespaciais"
        ],

        ("Ciências", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Propriedades da matéria", "Estados físicos da matéria",
            "Mudanças de estado físico", "Substâncias e misturas",
            "Métodos de separação", "Transformações físicas",
            "Transformações químicas", "Organização celular", "Microscopia",
            "Sistema ósseo", "Sistema muscular", "Órgãos dos sentidos",
            "Sistema nervoso central", "Drogas e sistema nervoso",
            "Forma e estrutura da Terra", "Fósseis", "Placas tectônicas",
            "Sistema Solar", "Movimentos da Terra", "Lua"
        ],
        ("Ciências", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Máquinas simples", "Calor e temperatura", "Propagação do calor",
            "Equilíbrio térmico", "Combustíveis", "Efeito estufa",
            "Camada de ozônio", "Vírus", "Bactérias", "Protozoários",
            "Fungos", "Vacinas", "Ecossistemas", "Cadeias alimentares",
            "Teias alimentares", "Biomas brasileiros", "Impactos ambientais",
            "Atmosfera", "Fenômenos naturais", "Placas tectônicas"
        ],
        ("Ciências", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Fontes e tipos de energia", "Energia elétrica", "Circuitos elétricos",
            "Consumo de energia", "Reprodução humana", "Puberdade",
            "Sistema reprodutor masculino", "Sistema reprodutor feminino",
            "Métodos contraceptivos", "Infecções sexualmente transmissíveis",
            "Hereditariedade", "Genética básica", "Clima e tempo",
            "Previsão do tempo", "Sistema Sol, Terra e Lua",
            "Fases da Lua", "Eclipses", "Estações do ano",
            "Movimentos da Terra", "Saúde e sexualidade"
        ],
        ("Ciências", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Átomos e moléculas", "Elementos químicos", "Tabela periódica",
            "Ligações químicas", "Reações químicas", "Leis ponderais",
            "Estrutura da matéria", "Ondas", "Som", "Luz",
            "Espectro eletromagnético", "Genética", "Leis de Mendel",
            "Evolução", "Seleção natural", "Diversidade biológica",
            "Sistema Solar", "Estrelas", "Galáxias", "Universo"
        ],

        ("Língua Inglesa", "Ensino Fundamental - Anos Finais", "6º ano"): [
            "Greetings", "Personal information", "Alphabet", "Numbers",
            "Colors", "School objects", "Family", "Verb to be",
            "Personal pronouns", "Possessive adjectives", "Simple present",
            "Daily routine", "Days and months", "Reading comprehension"
        ],
        ("Língua Inglesa", "Ensino Fundamental - Anos Finais", "7º ano"): [
            "Simple present", "Present continuous", "There is and there are",
            "Prepositions of place", "Countable and uncountable nouns",
            "Some and any", "Can and cannot", "Adverbs of frequency",
            "Sports and leisure", "Food and drinks", "Reading comprehension",
            "Text genres"
        ],
        ("Língua Inglesa", "Ensino Fundamental - Anos Finais", "8º ano"): [
            "Simple past", "Regular and irregular verbs", "Past continuous",
            "Comparatives", "Superlatives", "Future with will",
            "Going to", "Modal verbs", "Technology vocabulary",
            "Environment vocabulary", "Reading comprehension", "Text genres"
        ],
        ("Língua Inglesa", "Ensino Fundamental - Anos Finais", "9º ano"): [
            "Present perfect", "Past perfect", "Conditional sentences",
            "Passive voice", "Reported speech", "Relative pronouns",
            "Modal verbs", "Phrasal verbs", "Global issues",
            "Media and communication", "Reading comprehension", "Text genres"
        ],

        # =====================================================
        # ENSINO MÉDIO
        # =====================================================
        ("Língua Portuguesa", "Ensino Médio", "1ª série"): [
            "Gêneros discursivos", "Funções da linguagem", "Variação linguística",
            "Fonologia", "Morfologia", "Estrutura e formação de palavras",
            "Substantivos", "Adjetivos", "Pronomes", "Verbos",
            "Trovadorismo", "Humanismo", "Classicismo", "Quinhentismo",
            "Barroco", "Arcadismo", "Interpretação textual",
            "Coesão e coerência", "Introdução à redação do ENEM"
        ],
        ("Língua Portuguesa", "Ensino Médio", "2ª série"): [
            "Sintaxe do período simples", "Termos da oração",
            "Coordenação", "Subordinação", "Concordância verbal",
            "Concordância nominal", "Regência verbal", "Regência nominal",
            "Crase", "Romantismo", "Realismo", "Naturalismo",
            "Parnasianismo", "Simbolismo", "Pré-Modernismo",
            "Interpretação textual", "Argumentação", "Redação do ENEM"
        ],
        ("Língua Portuguesa", "Ensino Médio", "3ª série"): [
            "Modernismo - primeira fase", "Modernismo - segunda fase",
            "Modernismo - terceira fase", "Literatura contemporânea",
            "Orações subordinadas", "Pontuação", "Colocação pronominal",
            "Semântica", "Figuras de linguagem", "Intertextualidade",
            "Gêneros digitais", "Tese e repertório sociocultural",
            "Competências da redação do ENEM", "Coesão argumentativa",
            "Proposta de intervenção", "Revisão gramatical"
        ],

        ("Matemática", "Ensino Médio", "1ª série"): [
            "Conjuntos", "Conjuntos numéricos", "Intervalos reais",
            "Função afim", "Função quadrática", "Função modular",
            "Função exponencial", "Função logarítmica", "Progressão aritmética",
            "Progressão geométrica", "Razão e proporção", "Porcentagem",
            "Trigonometria no triângulo retângulo", "Geometria plana",
            "Semelhança de triângulos", "Estatística básica"
        ],
        ("Matemática", "Ensino Médio", "2ª série"): [
            "Trigonometria no ciclo", "Funções trigonométricas",
            "Matrizes", "Determinantes", "Sistemas lineares",
            "Análise combinatória", "Probabilidade", "Binômio de Newton",
            "Geometria espacial", "Prismas", "Pirâmides", "Cilindros",
            "Cones", "Esferas", "Áreas e volumes", "Estatística"
        ],
        ("Matemática", "Ensino Médio", "3ª série"): [
            "Geometria analítica", "Distância entre pontos", "Ponto médio",
            "Equação da reta", "Circunferência", "Números complexos",
            "Polinômios", "Equações polinomiais", "Matemática financeira",
            "Juros simples", "Juros compostos", "Probabilidade",
            "Estatística", "Funções", "Revisão ENEM"
        ],

        ("História", "Ensino Médio", "1ª série"): [
            "Pré-História", "Antiguidade Oriental", "Grécia Antiga",
            "Roma Antiga", "África Antiga", "Idade Média",
            "Feudalismo", "Império Bizantino", "Islamismo",
            "Cruzadas", "Renascimento comercial e urbano",
            "Renascimento cultural", "Reformas religiosas",
            "Formação dos Estados Nacionais", "Absolutismo",
            "Mercantilismo", "Expansão marítima"
        ],
        ("História", "Ensino Médio", "2ª série"): [
            "Colonização da América", "Brasil Colonial", "Escravidão",
            "Iluminismo", "Revolução Industrial", "Independência dos Estados Unidos",
            "Revolução Francesa", "Era Napoleônica", "Independências da América",
            "Primeiro Reinado", "Período Regencial", "Segundo Reinado",
            "Imperialismo", "Unificações italiana e alemã",
            "Abolição e Proclamação da República"
        ],
        ("História", "Ensino Médio", "3ª série"): [
            "Primeira República", "Primeira Guerra Mundial",
            "Revolução Russa", "Crise de 1929", "Nazifascismo",
            "Era Vargas", "Segunda Guerra Mundial", "Guerra Fria",
            "Descolonização afro-asiática", "Ditadura Militar no Brasil",
            "Redemocratização", "Nova República", "Globalização",
            "Conflitos contemporâneos", "História do Tocantins"
        ],

        ("Geografia", "Ensino Médio", "1ª série"): [
            "Cartografia", "Escalas", "Projeções cartográficas",
            "Geologia", "Relevo", "Solos", "Climatologia",
            "Hidrografia", "Biomas", "Questões ambientais",
            "Demografia", "Urbanização", "Espaço rural",
            "Fontes de energia", "Geografia do Brasil"
        ],
        ("Geografia", "Ensino Médio", "2ª série"): [
            "Industrialização", "Globalização", "Redes e fluxos",
            "Comércio internacional", "Blocos econômicos", "Geopolítica",
            "Estados Unidos", "Europa", "Rússia", "África",
            "América Latina", "Oriente Médio", "Ásia",
            "China", "Índia", "Japão"
        ],
        ("Geografia", "Ensino Médio", "3ª série"): [
            "Nova ordem mundial", "Conflitos internacionais",
            "Organizações internacionais", "Migrações internacionais",
            "Desigualdades socioespaciais", "Mudanças climáticas",
            "Questões energéticas", "Agronegócio", "Urbanização brasileira",
            "Industrialização brasileira", "Regiões brasileiras",
            "Amazônia", "Cerrado", "Geografia do Tocantins",
            "Revisão ENEM"
        ],

        ("Biologia", "Ensino Médio", "1ª série"): [
            "Origem da vida", "Bioquímica celular", "Citologia",
            "Membrana plasmática", "Organelas celulares", "Metabolismo energético",
            "Fotossíntese", "Respiração celular", "Divisão celular",
            "Histologia animal", "Vírus", "Bactérias", "Protozoários",
            "Fungos", "Botânica"
        ],
        ("Biologia", "Ensino Médio", "2ª série"): [
            "Zoologia", "Fisiologia humana", "Sistema digestório",
            "Sistema respiratório", "Sistema circulatório", "Sistema excretor",
            "Sistema nervoso", "Sistema endócrino", "Sistema reprodutor",
            "Embriologia", "Imunologia", "Doenças infecciosas",
            "Parasitologia", "Saúde pública"
        ],
        ("Biologia", "Ensino Médio", "3ª série"): [
            "Genética", "Leis de Mendel", "Herança ligada ao sexo",
            "Biotecnologia", "Evolução", "Seleção natural",
            "Especiação", "Ecologia", "Cadeias e teias alimentares",
            "Ciclos biogeoquímicos", "Relações ecológicas",
            "Sucessão ecológica", "Biomas brasileiros",
            "Impactos ambientais", "Revisão ENEM"
        ],

        ("Física", "Ensino Médio", "1ª série"): [
            "Grandezas físicas", "Vetores", "Cinemática",
            "Movimento uniforme", "Movimento uniformemente variado",
            "Queda livre", "Lançamentos", "Dinâmica",
            "Leis de Newton", "Força de atrito", "Trabalho",
            "Energia", "Potência", "Impulso", "Quantidade de movimento"
        ],
        ("Física", "Ensino Médio", "2ª série"): [
            "Hidrostática", "Pressão", "Princípio de Pascal",
            "Princípio de Arquimedes", "Termologia", "Calorimetria",
            "Dilatação térmica", "Termodinâmica", "Ondulatória",
            "Som", "Óptica geométrica", "Espelhos", "Lentes",
            "Refração", "Instrumentos ópticos"
        ],
        ("Física", "Ensino Médio", "3ª série"): [
            "Eletrostática", "Campo elétrico", "Potencial elétrico",
            "Corrente elétrica", "Resistores", "Circuitos elétricos",
            "Potência elétrica", "Magnetismo", "Eletromagnetismo",
            "Indução eletromagnética", "Física moderna", "Relatividade",
            "Física quântica", "Radioatividade", "Revisão ENEM"
        ],

        ("Química", "Ensino Médio", "1ª série"): [
            "Matéria e energia", "Estados físicos", "Misturas",
            "Separação de misturas", "Modelos atômicos", "Estrutura atômica",
            "Tabela periódica", "Propriedades periódicas", "Ligações químicas",
            "Geometria molecular", "Forças intermoleculares",
            "Funções inorgânicas", "Reações químicas", "Balanceamento"
        ],
        ("Química", "Ensino Médio", "2ª série"): [
            "Mol", "Massa molar", "Estequiometria", "Soluções",
            "Concentração", "Diluição", "Mistura de soluções",
            "Termoquímica", "Cinética química", "Equilíbrio químico",
            "Equilíbrio iônico", "pH e pOH", "Hidrólise salina",
            "Eletroquímica"
        ],
        ("Química", "Ensino Médio", "3ª série"): [
            "Química orgânica", "Cadeias carbônicas", "Funções orgânicas",
            "Isomeria", "Reações orgânicas", "Polímeros",
            "Petróleo", "Bioquímica", "Radioatividade",
            "Química ambiental", "Química dos alimentos",
            "Combustíveis", "Pilhas e eletrólise", "Revisão ENEM"
        ],

        ("Filosofia", "Ensino Médio", "1ª série"): [
            "Origem da Filosofia", "Mito e razão", "Pré-socráticos",
            "Sócrates", "Platão", "Aristóteles", "Filosofia helenística",
            "Ética antiga", "Política na Antiguidade", "Conhecimento e verdade"
        ],
        ("Filosofia", "Ensino Médio", "2ª série"): [
            "Filosofia medieval", "Patrística", "Escolástica",
            "Racionalismo", "Empirismo", "Iluminismo", "Kant",
            "Filosofia política moderna", "Contrato social", "Ética moderna"
        ],
        ("Filosofia", "Ensino Médio", "3ª série"): [
            "Idealismo", "Marxismo", "Nietzsche", "Fenomenologia",
            "Existencialismo", "Escola de Frankfurt", "Filosofia da ciência",
            "Bioética", "Filosofia contemporânea", "Cidadania e democracia"
        ],

        ("Sociologia", "Ensino Médio", "1ª série"): [
            "Introdução à Sociologia", "Socialização", "Cultura",
            "Identidade", "Etnocentrismo", "Relativismo cultural",
            "Instituições sociais", "Grupos sociais", "Estratificação social",
            "Desigualdade social"
        ],
        ("Sociologia", "Ensino Médio", "2ª série"): [
            "Trabalho e sociedade", "Capitalismo", "Classes sociais",
            "Karl Marx", "Émile Durkheim", "Max Weber",
            "Poder e política", "Estado", "Democracia",
            "Movimentos sociais", "Cidadania"
        ],
        ("Sociologia", "Ensino Médio", "3ª série"): [
            "Globalização", "Indústria cultural", "Mídia e sociedade",
            "Consumo", "Gênero e sociedade", "Relações étnico-raciais",
            "Violência", "Urbanização", "Meio ambiente",
            "Direitos humanos", "Juventude e participação social"
        ],

        ("Língua Inglesa", "Ensino Médio", "1ª série"): [
            "Reading strategies", "Cognates and false cognates",
            "Simple present", "Present continuous", "Simple past",
            "Pronouns", "Articles", "Prepositions", "Text genres",
            "Vocabulary in context", "Interpretação de textos"
        ],
        ("Língua Inglesa", "Ensino Médio", "2ª série"): [
            "Present perfect", "Past continuous", "Future forms",
            "Modal verbs", "Comparatives and superlatives",
            "Passive voice", "Relative pronouns", "Conditionals",
            "Text genres", "Interpretação de textos"
        ],
        ("Língua Inglesa", "Ensino Médio", "3ª série"): [
            "Reported speech", "Passive voice", "Conditionals",
            "Phrasal verbs", "Linking words", "Argumentative texts",
            "Scientific texts", "Media texts", "ENEM reading strategies",
            "Interpretação de textos"
        ]
    }

    for (disciplina_assunto, etapa_assunto, serie_assunto), nomes_assuntos in assuntos_ampliados.items():
        for nome_assunto in nomes_assuntos:
            cursor.execute("""
                INSERT INTO assuntos (
                    escola_id,
                    disciplina,
                    etapa_ensino,
                    ano_serie,
                    nome,
                    ativo
                )
                SELECT NULL, ?, ?, ?, ?, 1
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM assuntos
                    WHERE escola_id IS NULL
                      AND disciplina = ?
                      AND etapa_ensino = ?
                      AND ano_serie = ?
                      AND nome = ?
                )
            """, (
                disciplina_assunto, etapa_assunto, serie_assunto, nome_assunto,
                disciplina_assunto, etapa_assunto, serie_assunto, nome_assunto
            ))

    # Catálogo completo do Ensino Fundamental - Anos Iniciais.
    # Abrange do 1º ao 5º ano e os componentes curriculares mais comuns.
    assuntos_anos_iniciais = {
        # =====================================================
        # LÍNGUA PORTUGUESA
        # =====================================================
        ("Língua Portuguesa", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Alfabeto", "Ordem alfabética", "Vogais e consoantes",
            "Consciência fonológica", "Sílabas", "Formação de palavras",
            "Palavras e imagens", "Leitura de palavras", "Leitura de frases",
            "Escrita do nome", "Letra maiúscula e minúscula",
            "Segmentação de palavras", "Rimas", "Parlendas", "Cantigas",
            "Trava-línguas", "Adivinhas", "Listas", "Bilhetes",
            "Contos infantis", "Interpretação de texto", "Produção de frases"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Ordem alfabética", "Sílabas simples e complexas",
            "Separação silábica", "Classificação quanto ao número de sílabas",
            "Substantivos", "Nomes próprios e comuns", "Gênero do substantivo",
            "Número do substantivo", "Adjetivos", "Artigos",
            "Sinônimos e antônimos", "Ortografia", "Pontuação",
            "Frase e parágrafo", "Leitura e interpretação",
            "Fábulas", "Contos", "Poemas", "Bilhetes", "Convites",
            "Receitas", "Histórias em quadrinhos", "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Substantivos próprios e comuns", "Substantivos coletivos",
            "Adjetivos", "Artigos", "Pronomes pessoais", "Verbos",
            "Tempos verbais", "Sílaba tônica", "Acentuação gráfica",
            "Uso de M e N", "Uso de R e RR", "Uso de S, SS, C e Ç",
            "Pontuação", "Frase, oração e parágrafo",
            "Leitura e interpretação", "Contos", "Fábulas", "Lendas",
            "Notícias", "Cartas", "Receitas", "Poemas",
            "Histórias em quadrinhos", "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Substantivos", "Adjetivos", "Artigos", "Pronomes",
            "Verbos e tempos verbais", "Advérbios", "Preposições",
            "Concordância nominal", "Concordância verbal",
            "Sílaba tônica", "Acentuação", "Encontro vocálico",
            "Encontro consonantal", "Dígrafos", "Ortografia",
            "Pontuação", "Discurso direto e indireto",
            "Leitura e interpretação", "Conto", "Crônica", "Notícia",
            "Reportagem", "Poema", "Carta", "Resumo", "Produção textual"
        ],
        ("Língua Portuguesa", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Classes gramaticais", "Substantivos e classificações",
            "Adjetivos e locuções adjetivas", "Pronomes", "Numerais",
            "Verbos e conjugações", "Tempos e modos verbais",
            "Advérbios", "Preposições", "Conjunções",
            "Sujeito e predicado", "Tipos de sujeito", "Concordância verbal",
            "Concordância nominal", "Acentuação gráfica", "Crase",
            "Pontuação", "Figuras de linguagem", "Denotação e conotação",
            "Leitura e interpretação", "Gêneros textuais",
            "Artigo de opinião", "Notícia", "Reportagem", "Crônica",
            "Poema", "Biografia", "Produção textual"
        ],

        # =====================================================
        # MATEMÁTICA
        # =====================================================
        ("Matemática", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Números até 10", "Números até 20", "Números até 100",
            "Contagem", "Sequência numérica", "Antecessor e sucessor",
            "Comparação de quantidades", "Maior, menor e igual",
            "Adição", "Subtração", "Situações-problema",
            "Dezenas e unidades", "Formas geométricas planas",
            "Localização e posição", "Noções de comprimento",
            "Noções de massa", "Noções de capacidade", "Calendário",
            "Dias da semana", "Horas", "Sistema monetário",
            "Tabelas e gráficos simples"
        ],
        ("Matemática", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Números até 100", "Números até 1000", "Valor posicional",
            "Centenas, dezenas e unidades", "Composição e decomposição",
            "Adição com e sem reagrupamento", "Subtração com e sem reagrupamento",
            "Multiplicação como adição de parcelas iguais",
            "Divisão como repartição", "Dobro", "Triplo", "Metade",
            "Situações-problema", "Sequências", "Figuras geométricas",
            "Sólidos geométricos", "Comprimento", "Massa", "Capacidade",
            "Tempo", "Calendário", "Sistema monetário", "Tabelas e gráficos"
        ],
        ("Matemática", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Números naturais", "Sistema de numeração decimal",
            "Valor posicional", "Composição e decomposição",
            "Adição", "Subtração", "Multiplicação", "Divisão",
            "Tabuada", "Problemas com as quatro operações",
            "Frações simples", "Metade, terça parte e quarta parte",
            "Sequências numéricas", "Par e ímpar", "Figuras planas",
            "Sólidos geométricos", "Perímetro", "Comprimento",
            "Massa", "Capacidade", "Tempo", "Sistema monetário",
            "Leitura de tabelas", "Leitura de gráficos"
        ],
        ("Matemática", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Números naturais", "Ordens e classes", "Valor posicional",
            "Composição e decomposição", "Adição", "Subtração",
            "Multiplicação", "Divisão", "Expressões numéricas",
            "Múltiplos e divisores", "Frações", "Frações equivalentes",
            "Números decimais", "Problemas com as quatro operações",
            "Ângulos", "Retas", "Polígonos", "Perímetro", "Área",
            "Medidas de comprimento", "Massa", "Capacidade", "Tempo",
            "Sistema monetário", "Tabelas e gráficos"
        ],
        ("Matemática", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Sistema de numeração decimal", "Ordens e classes",
            "Números naturais", "Números decimais", "Adição e subtração",
            "Multiplicação e divisão", "Expressões numéricas",
            "Múltiplos e divisores", "Números primos", "MMC e MDC",
            "Frações", "Operações com frações", "Porcentagem",
            "Razão e proporção", "Regra de três simples",
            "Plano cartesiano", "Ângulos", "Polígonos", "Triângulos",
            "Quadriláteros", "Perímetro", "Área", "Volume",
            "Grandezas e medidas", "Média aritmética",
            "Tabelas, gráficos e probabilidade"
        ],

        # =====================================================
        # CIÊNCIAS
        # =====================================================
        ("Ciências", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Corpo humano", "Partes do corpo", "Órgãos dos sentidos",
            "Hábitos de higiene", "Alimentação saudável", "Saúde",
            "Seres vivos e não vivos", "Animais", "Plantas",
            "Ambientes", "Dia e noite", "Sol", "Lua",
            "Água", "Ar", "Solo", "Materiais do cotidiano",
            "Cuidados com o ambiente", "Lixo e reciclagem"
        ],
        ("Ciências", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Seres vivos", "Ciclo de vida", "Animais vertebrados e invertebrados",
            "Habitat dos animais", "Plantas e suas partes",
            "Germinação", "Necessidades das plantas", "Corpo humano",
            "Higiene e saúde", "Alimentação", "Água", "Ar", "Solo",
            "Luz e sombra", "Dia e noite", "Materiais e objetos",
            "Reutilização e reciclagem", "Preservação ambiental"
        ],
        ("Ciências", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Características dos animais", "Classificação dos animais",
            "Alimentação dos animais", "Reprodução dos animais",
            "Plantas", "Fotossíntese", "Cadeia alimentar",
            "Corpo humano", "Sistema locomotor", "Órgãos dos sentidos",
            "Saúde e prevenção", "Estados físicos da água",
            "Ciclo da água", "Ar e atmosfera", "Solo",
            "Som", "Luz", "Terra", "Lua", "Sistema Solar",
            "Impactos ambientais"
        ],
        ("Ciências", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Célula", "Seres unicelulares e pluricelulares",
            "Microrganismos", "Cadeias alimentares", "Relações ecológicas",
            "Ecossistemas", "Corpo humano", "Sistema digestório",
            "Sistema respiratório", "Sistema circulatório",
            "Hábitos saudáveis", "Misturas", "Transformações da matéria",
            "Água e saneamento", "Solo", "Rochas e minerais",
            "Pontos cardeais", "Movimentos da Terra", "Fases da Lua",
            "Preservação ambiental"
        ],
        ("Ciências", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Matéria e energia", "Propriedades da matéria",
            "Misturas e separação", "Transformações físicas e químicas",
            "Ciclo da água", "Uso sustentável da água",
            "Nutrição", "Sistema digestório", "Sistema respiratório",
            "Sistema circulatório", "Sistema excretor",
            "Alimentação saudável", "Reprodução humana",
            "Puberdade", "Sistema Solar", "Movimentos da Terra",
            "Fases da Lua", "Constelações", "Fontes de energia",
            "Consumo consciente", "Reciclagem e sustentabilidade"
        ],

        # =====================================================
        # HISTÓRIA
        # =====================================================
        ("História", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Identidade", "História pessoal", "Nome e sobrenome",
            "Família", "Diferentes tipos de família", "Linha do tempo",
            "Memórias", "Brinquedos e brincadeiras", "Escola",
            "Regras de convivência", "Casa e moradia", "Bairro",
            "Datas comemorativas", "Direitos das crianças"
        ],
        ("História", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "História pessoal e familiar", "Documentos pessoais",
            "Fontes históricas", "Memórias familiares", "Comunidade",
            "Bairro", "Profissões", "Trabalho no passado e no presente",
            "Meios de transporte", "Meios de comunicação",
            "Brincadeiras antigas e atuais", "Mudanças e permanências",
            "Patrimônio cultural", "Direitos e deveres"
        ],
        ("História", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Município", "História da cidade", "Formação da comunidade",
            "Grupos sociais", "Povos indígenas", "Povos africanos",
            "Imigração", "Trabalho e profissões", "Espaços públicos",
            "Patrimônio histórico", "Patrimônio cultural",
            "Festas e tradições", "Mudanças na cidade",
            "Poder público municipal", "Cidadania"
        ],
        ("História", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Nomadismo e sedentarização", "Primeiros grupos humanos",
            "Povos indígenas do Brasil", "Grandes navegações",
            "Chegada dos portugueses", "Colonização do Brasil",
            "Escravidão indígena", "Escravidão africana",
            "Economia açucareira", "Bandeirantes", "Mineração",
            "Formação do território brasileiro", "Migrações",
            "Patrimônio histórico", "Diversidade cultural"
        ],
        ("História", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Povos e culturas antigas", "Formação das primeiras cidades",
            "Cidadania na Antiguidade", "Grécia Antiga", "Roma Antiga",
            "Democracia", "Direitos humanos", "Constituição",
            "Formação do povo brasileiro", "Povos indígenas",
            "Povos africanos", "Imigração no Brasil",
            "Abolição da escravidão", "Proclamação da República",
            "Patrimônio material e imaterial", "Diversidade religiosa",
            "Cidadania e participação social"
        ],

        # =====================================================
        # GEOGRAFIA
        # =====================================================
        ("Geografia", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Lugar de vivência", "Casa", "Escola", "Bairro",
            "Paisagem", "Elementos naturais e culturais",
            "Localização", "Direita e esquerda", "Frente e atrás",
            "Perto e longe", "Trajetos", "Meios de transporte",
            "Meios de comunicação", "Campo e cidade",
            "Tempo atmosférico", "Cuidados com o ambiente"
        ],
        ("Geografia", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Lugar e paisagem", "Bairro", "Zona urbana e rural",
            "Tipos de moradia", "Trabalho no campo e na cidade",
            "Meios de transporte", "Meios de comunicação",
            "Representação dos lugares", "Mapas simples",
            "Pontos de referência", "Orientação", "Recursos naturais",
            "Água", "Solo", "Vegetação", "Impactos ambientais"
        ],
        ("Geografia", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Município", "Cidade e campo", "Paisagens urbanas e rurais",
            "Atividades econômicas", "Agricultura", "Pecuária",
            "Indústria", "Comércio e serviços", "Trabalho",
            "Representação cartográfica", "Mapas", "Legendas",
            "Pontos cardeais", "Relevo", "Hidrografia",
            "Vegetação", "Clima", "Problemas ambientais"
        ],
        ("Geografia", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Território brasileiro", "Divisão política do Brasil",
            "Estados e capitais", "Regiões brasileiras",
            "Município e estado", "População brasileira",
            "Migrações", "Diversidade cultural", "Campo e cidade",
            "Urbanização", "Atividades econômicas", "Agropecuária",
            "Indústria", "Comércio", "Relevo brasileiro",
            "Clima", "Hidrografia", "Biomas brasileiros",
            "Questões ambientais"
        ],
        ("Geografia", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Território e fronteiras", "Regiões brasileiras",
            "População brasileira", "Distribuição da população",
            "Migrações internas", "Urbanização", "Rede urbana",
            "Industrialização", "Agropecuária", "Fontes de energia",
            "Transportes e comunicação", "Cartografia",
            "Escala", "Coordenadas geográficas", "Relevo",
            "Clima", "Hidrografia", "Biomas",
            "Desigualdades regionais", "Sustentabilidade"
        ],

        # =====================================================
        # ARTE
        # =====================================================
        ("Arte", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Cores primárias", "Formas", "Linhas", "Texturas",
            "Desenho", "Pintura", "Colagem", "Modelagem",
            "Música e sons", "Expressão corporal", "Teatro",
            "Brincadeiras cantadas", "Arte e identidade"
        ],
        ("Arte", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Cores primárias e secundárias", "Mistura de cores",
            "Formas geométricas na arte", "Texturas", "Desenho",
            "Pintura", "Colagem", "Escultura", "Música",
            "Ritmo", "Dança", "Teatro", "Arte popular"
        ],
        ("Arte", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Elementos visuais", "Linha, forma e cor", "Luz e sombra",
            "Desenho de observação", "Pintura", "Gravura",
            "Escultura", "Fotografia", "Música", "Ritmo e melodia",
            "Dança", "Teatro", "Cultura popular", "Arte indígena"
        ],
        ("Arte", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Artes visuais", "Composição visual", "Perspectiva",
            "Pintura", "Escultura", "Gravura", "Fotografia",
            "Música brasileira", "Instrumentos musicais", "Dança",
            "Teatro", "Arte indígena", "Arte africana",
            "Patrimônio cultural"
        ],
        ("Arte", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Elementos da linguagem visual", "História da arte",
            "Arte brasileira", "Arte indígena", "Arte afro-brasileira",
            "Arte popular", "Pintura", "Escultura", "Fotografia",
            "Cinema", "Música", "Dança", "Teatro",
            "Cultura e patrimônio", "Produção artística"
        ],

        # =====================================================
        # EDUCAÇÃO FÍSICA
        # =====================================================
        ("Educação Física", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Brincadeiras e jogos", "Coordenação motora", "Equilíbrio",
            "Lateralidade", "Esquema corporal", "Movimentos básicos",
            "Jogos cooperativos", "Ritmo", "Expressão corporal"
        ],
        ("Educação Física", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Brincadeiras populares", "Jogos de regras simples",
            "Coordenação motora", "Agilidade", "Equilíbrio",
            "Lateralidade", "Ginástica geral", "Dança",
            "Jogos cooperativos", "Hábitos saudáveis"
        ],
        ("Educação Física", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Jogos populares", "Jogos cooperativos", "Esportes de marca",
            "Esportes de precisão", "Ginástica", "Dança",
            "Lutas de contexto comunitário", "Coordenação motora",
            "Capacidades físicas", "Saúde e movimento"
        ],
        ("Educação Física", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Jogos e brincadeiras tradicionais", "Esportes de invasão",
            "Esportes de rede e parede", "Esportes de campo e taco",
            "Ginástica geral", "Danças populares", "Lutas",
            "Capacidades físicas", "Regras esportivas",
            "Saúde e qualidade de vida"
        ],
        ("Educação Física", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Jogos eletrônicos", "Esportes de invasão", "Esportes de rede",
            "Esportes de marca", "Esportes de precisão",
            "Ginástica geral", "Danças do Brasil", "Lutas do Brasil",
            "Capacidades físicas", "Fair play", "Regras e arbitragem",
            "Saúde, exercício e qualidade de vida"
        ],

        # =====================================================
        # ENSINO RELIGIOSO
        # =====================================================
        ("Ensino Religioso", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Identidade", "Respeito", "Convivência", "Família",
            "Amizade", "Diferenças", "Valores", "Solidariedade",
            "Símbolos religiosos", "Festas e celebrações"
        ],
        ("Ensino Religioso", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Identidade e alteridade", "Respeito às diferenças",
            "Família e comunidade", "Valores humanos", "Solidariedade",
            "Símbolos religiosos", "Espaços sagrados",
            "Festas religiosas", "Tradições culturais"
        ],
        ("Ensino Religioso", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Diversidade religiosa", "Tradições religiosas",
            "Espaços sagrados", "Práticas religiosas", "Orações",
            "Festas e celebrações", "Valores éticos",
            "Respeito e tolerância", "Cultura de paz"
        ],
        ("Ensino Religioso", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Religiões do mundo", "Textos sagrados", "Símbolos religiosos",
            "Ritos e celebrações", "Lideranças religiosas",
            "Tradições indígenas", "Tradições afro-brasileiras",
            "Ética", "Direitos humanos", "Cultura de paz"
        ],
        ("Ensino Religioso", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Diversidade de crenças", "Religiões monoteístas",
            "Religiões orientais", "Matrizes indígenas",
            "Matrizes africanas", "Textos sagrados",
            "Mitos e narrativas religiosas", "Ética e cidadania",
            "Liberdade religiosa", "Diálogo inter-religioso"
        ],

        # =====================================================
        # LÍNGUA INGLESA
        # =====================================================
        ("Língua Inglesa", "Ensino Fundamental - Anos Iniciais", "1º ano"): [
            "Greetings", "Colors", "Numbers 1 to 10", "School objects",
            "Family", "Body parts", "Animals", "Songs and games"
        ],
        ("Língua Inglesa", "Ensino Fundamental - Anos Iniciais", "2º ano"): [
            "Greetings", "Alphabet", "Numbers", "Colors",
            "Family members", "School objects", "Toys",
            "Animals", "Food", "Days of the week"
        ],
        ("Língua Inglesa", "Ensino Fundamental - Anos Iniciais", "3º ano"): [
            "Personal information", "Numbers", "Colors",
            "Family", "Parts of the house", "School subjects",
            "Food and drinks", "Animals", "Clothes",
            "Days and months", "Verb to be"
        ],
        ("Língua Inglesa", "Ensino Fundamental - Anos Iniciais", "4º ano"): [
            "Personal information", "Daily routine", "Simple present",
            "Family", "House", "School", "Food",
            "Clothes", "Weather", "Places in town",
            "Prepositions of place", "Can and cannot"
        ],
        ("Língua Inglesa", "Ensino Fundamental - Anos Iniciais", "5º ano"): [
            "Simple present", "Daily routine", "Adverbs of frequency",
            "Personal pronouns", "Possessive adjectives",
            "There is and there are", "Places in town",
            "Food and drinks", "Sports", "Weather",
            "Dates and time", "Reading comprehension"
        ]
    }

    for (disciplina_assunto, etapa_assunto, serie_assunto), nomes_assuntos in assuntos_anos_iniciais.items():
        for nome_assunto in nomes_assuntos:
            cursor.execute("""
                INSERT INTO assuntos (
                    escola_id,
                    disciplina,
                    etapa_ensino,
                    ano_serie,
                    nome,
                    ativo
                )
                SELECT NULL, ?, ?, ?, ?, 1
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM assuntos
                    WHERE escola_id IS NULL
                      AND disciplina = ?
                      AND etapa_ensino = ?
                      AND ano_serie = ?
                      AND nome = ?
                )
            """, (
                disciplina_assunto, etapa_assunto, serie_assunto, nome_assunto,
                disciplina_assunto, etapa_assunto, serie_assunto, nome_assunto
            ))

    garantir_coluna("escolas", "tipo_instituicao", "TEXT")
    garantir_coluna("usuarios", "escola_id", "INTEGER")
    garantir_coluna("usuarios", "cpf", "TEXT")
    garantir_coluna("usuarios", "foto", "TEXT")
    garantir_coluna("turmas", "escola_id", "INTEGER")
    garantir_coluna("turmas", "etapa", "TEXT")
    garantir_coluna("turmas", "ano_letivo", "TEXT")
    garantir_coluna("turmas", "ano_letivo_id", "INTEGER")
    garantir_coluna("alunos", "escola_id", "INTEGER")
    garantir_coluna("alunos", "ano_letivo_id", "INTEGER")
    garantir_coluna("professores", "escola_id", "INTEGER")
    garantir_coluna("questoes", "escola_id", "INTEGER")
    garantir_coluna("questoes", "assunto", "TEXT")
    garantir_coluna("questoes", "assunto_temporario", "INTEGER DEFAULT 0")
    garantir_coluna("questoes", "tipo_questao", "TEXT DEFAULT 'multipla_escolha'")
    garantir_coluna("questoes", "enunciado_html", "TEXT")
    garantir_coluna("questoes", "alternativas_json", "TEXT")
    garantir_coluna("questoes", "respostas_corretas", "TEXT")
    garantir_coluna("questoes", "resposta_esperada", "TEXT")
    garantir_coluna("questoes", "criterios_correcao", "TEXT")
    garantir_coluna("questoes", "observacoes", "TEXT")
    garantir_coluna("questoes", "criado_por", "INTEGER")
    garantir_coluna("questoes", "criado_em", "TEXT")
    garantir_coluna("questoes", "atualizado_em", "TEXT")
    garantir_coluna("questoes", "etapa_ensino", "TEXT")
    garantir_coluna("questoes", "ano_serie", "TEXT")
    garantir_coluna("questoes", "subassunto", "TEXT")
    garantir_coluna("questoes", "unidade_tematica", "TEXT")
    garantir_coluna("questoes", "objeto_conhecimento", "TEXT")
    garantir_coluna("questoes", "habilidade_bncc", "TEXT")
    garantir_coluna("questoes", "matriz_referencia", "TEXT")
    garantir_coluna("questoes", "descritor_saeb", "TEXT")
    garantir_coluna("questoes", "taxonomia_bloom", "TEXT")
    garantir_coluna("questoes", "fonte", "TEXT")
    garantir_coluna("questoes", "ano_fonte", "INTEGER")
    garantir_coluna("questoes", "tags", "TEXT")
    garantir_coluna("questoes", "tempo_estimado", "INTEGER")
    garantir_coluna("questoes", "linhas_resposta", "INTEGER DEFAULT 5")
    garantir_coluna("provas", "professor_id", "INTEGER")
    garantir_coluna("provas", "data_geracao", "TEXT")
    garantir_coluna("provas", "data_aplicacao", "TEXT")
    garantir_coluna("provas", "escola_id", "INTEGER")
    garantir_coluna("provas", "ano_letivo_id", "INTEGER")
    garantir_coluna("provas", "status", "TEXT DEFAULT 'rascunho'")
    garantir_coluna("provas", "atualizado_em", "TEXT")
    garantir_coluna("provas", "media_ativa", "INTEGER DEFAULT 0")
    garantir_coluna("provas", "media_aprovacao", "REAL")
    garantir_coluna("provas", "peso_total", "REAL DEFAULT 10")
    garantir_coluna("provas", "tipo_peso", "TEXT DEFAULT 'automatico'")
    garantir_coluna("prova_questoes", "peso", "REAL DEFAULT 0")
    garantir_coluna("prova_questoes", "ordem", "INTEGER DEFAULT 0")
    garantir_coluna("instituicao", "logo", "TEXT")
    garantir_coluna("permissoes", "pode_acessar", "INTEGER DEFAULT 0")

    garantir_coluna("componentes_curriculares", "escola_id", "INTEGER")
    garantir_coluna("componentes_curriculares", "etapa_ensino", "TEXT")
    garantir_coluna("componentes_curriculares", "nome", "TEXT")
    garantir_coluna(
        "componentes_curriculares",
        "tipo",
        "TEXT DEFAULT 'padrao'"
    )
    garantir_coluna(
        "componentes_curriculares",
        "ativo",
        "INTEGER DEFAULT 1"
    )

    cursor.execute("PRAGMA table_info(turmas)")
    colunas_turmas = {linha[1] for linha in cursor.fetchall()}

    if "etapa_ensino" in colunas_turmas and "etapa" in colunas_turmas:
        cursor.execute("""
            UPDATE turmas
            SET etapa = etapa_ensino
            WHERE (etapa IS NULL OR TRIM(etapa) = '')
              AND etapa_ensino IS NOT NULL
        """)

    cursor.execute("""
        UPDATE componentes_curriculares
        SET tipo = 'padrao'
        WHERE tipo IS NULL OR TRIM(tipo) = ''
    """)

    cursor.execute("""
        UPDATE componentes_curriculares
        SET ativo = 1
        WHERE ativo IS NULL
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_componentes_escola_etapa
        ON componentes_curriculares (escola_id, etapa_ensino)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_usuarios_escola
        ON usuarios (escola_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_turmas_escola
        ON turmas (escola_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_aluno_matriculas_aluno
        ON aluno_matriculas (aluno_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_aluno_matriculas_ano
        ON aluno_matriculas (ano_letivo_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_aluno_matriculas_turma
        ON aluno_matriculas (turma_id)
    """)

    cargos = [
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor",
        "Secretaria"
    ]

    for nome_cargo in cargos:
        cursor.execute(
            "INSERT OR IGNORE INTO cargos (nome) VALUES (?)",
            (nome_cargo,)
        )

    cursor.execute("""
        SELECT id
        FROM cargos
        WHERE nome = ?
        LIMIT 1
    """, ("Administrador Geral",))

    cargo_admin = cursor.fetchone()
    cargo_admin_id = cargo_admin[0] if cargo_admin else None

    if cargo_admin_id:
        cursor.execute("""
            INSERT OR IGNORE INTO usuarios (
                nome,
                email,
                senha,
                cargo_id,
                ativo
            )
            VALUES (?, ?, ?, ?, 1)
        """, (
            "Administrador",
            "admin",
            generate_password_hash("admin123"),
            cargo_admin_id
        ))

    # =====================================================
    # CONFIGURAÇÕES E AUDITORIA DO ANO LETIVO
    # =====================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracao_ano_letivo_global (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            ativo INTEGER NOT NULL DEFAULT 0,
            ano INTEGER,
            data_execucao TEXT,
            data_inicio TEXT,
            data_fim TEXT,
            copiar_turmas INTEGER NOT NULL DEFAULT 1,
            copiar_vinculos INTEGER NOT NULL DEFAULT 1,
            encerrar_anterior INTEGER NOT NULL DEFAULT 1,
            executado INTEGER NOT NULL DEFAULT 0,
            atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        INSERT OR IGNORE INTO configuracao_ano_letivo_global (
            id,
            ativo,
            copiar_turmas,
            copiar_vinculos,
            encerrar_anterior,
            executado
        )
        VALUES (1, 0, 1, 1, 1, 0)
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracao_ano_letivo_instituicao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            escola_id INTEGER NOT NULL UNIQUE,
            modo TEXT NOT NULL DEFAULT 'global',
            ativo INTEGER NOT NULL DEFAULT 0,
            ano INTEGER,
            data_execucao TEXT,
            data_inicio TEXT,
            data_fim TEXT,
            copiar_turmas INTEGER NOT NULL DEFAULT 1,
            copiar_vinculos INTEGER NOT NULL DEFAULT 1,
            encerrar_anterior INTEGER NOT NULL DEFAULT 1,
            executado INTEGER NOT NULL DEFAULT 0,
            atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ano_letivo_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            escola_id INTEGER,
            ano_letivo_id INTEGER,
            usuario_id INTEGER,
            acao TEXT NOT NULL,
            detalhes TEXT,
            criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE SET NULL,
            FOREIGN KEY (ano_letivo_id) REFERENCES anos_letivos(id) ON DELETE SET NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
        )
    """)

    banco.commit()
    banco.close()

# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def index():
    """
    Dashboard global da plataforma.

    Os totais acadêmicos (turmas, alunos e provas) obedecem ao ano
    selecionado na barra superior. Cadastros permanentes, como usuários,
    instituições, professores e questões, não são zerados ao trocar o ano.
    """

    if "usuario_id" not in session:
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    contexto = obter_contexto_plataforma()

    usuario_id = contexto["usuario_id"]
    usuario_cargo = contexto["cargo"]
    escola_id = contexto["escola_id"]
    ano_letivo_id = contexto["ano_letivo_id"]
    ano_visualizado = contexto["ano"]
    ano_letivo_id_ativo = contexto["ano_ativo_id"]
    ano_letivo_ativo = contexto["ano_ativo"]

    total_instituicoes = 0
    total_usuarios = 0
    total_professores = 0
    total_alunos = 0
    total_turmas = 0
    total_questoes = 0
    total_provas = 0

    nome_instituicao = None
    permissoes_usuario = []

    try:
        # =====================================================
        # PERMISSÕES DO USUÁRIO
        # =====================================================

        if usuario_cargo == "Administrador Geral":
            permissoes_usuario = [
                "Dashboard",
                "Instituições",
                "Usuários",
                "Anos Letivos",
                "Turmas",
                "Professores",
                "Alunos",
                "Questões",
                "Provas",
                "Relatórios"
            ]
        else:
            cursor.execute("""
                SELECT modulo
                FROM usuario_permissoes
                WHERE usuario_id = ?
                  AND pode_acessar = 1
            """, (usuario_id,))

            permissoes_usuario = [
                linha["modulo"]
                for linha in cursor.fetchall()
            ]

            if (
                usuario_cargo == "Administrador da Instituição"
                and "Anos Letivos" not in permissoes_usuario
            ):
                permissoes_usuario.append("Anos Letivos")

        # =====================================================
        # ADMINISTRADOR GERAL
        # Usa o número do ano, porque cada instituição possui
        # um ID próprio para o mesmo ano letivo.
        # =====================================================

        if usuario_cargo == "Administrador Geral":
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM escolas
                WHERE COALESCE(status, 1) = 1
            """)
            total_instituicoes = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM usuarios
                WHERE ativo = 1
            """)
            total_usuarios = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM professores
            """)
            total_professores = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM questoes
            """)
            total_questoes = cursor.fetchone()["total"]

            if ano_visualizado is not None:
                # Turmas do ano selecionado em todas as instituições.
                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM turmas AS t
                    INNER JOIN anos_letivos AS al
                        ON al.id = t.ano_letivo_id
                       AND al.escola_id = t.escola_id
                    WHERE al.ano = ?
                """, (ano_visualizado,))
                total_turmas = cursor.fetchone()["total"]

                # Alunos do ano selecionado. O UNION mantém compatibilidade
                # com os registros antigos da tabela alunos e com a nova
                # tabela de histórico aluno_matriculas.
                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM (
                        SELECT
                            am.aluno_id AS aluno_id,
                            am.escola_id AS escola_id
                        FROM aluno_matriculas AS am
                        INNER JOIN anos_letivos AS al
                            ON al.id = am.ano_letivo_id
                           AND al.escola_id = am.escola_id
                        WHERE al.ano = ?

                        UNION

                        SELECT
                            a.id AS aluno_id,
                            a.escola_id AS escola_id
                        FROM alunos AS a
                        INNER JOIN anos_letivos AS al
                            ON al.id = a.ano_letivo_id
                           AND al.escola_id = a.escola_id
                        WHERE al.ano = ?
                    ) AS alunos_do_ano
                """, (ano_visualizado, ano_visualizado))
                total_alunos = cursor.fetchone()["total"]

                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM provas AS p
                    INNER JOIN anos_letivos AS al
                        ON al.id = p.ano_letivo_id
                       AND al.escola_id = p.escola_id
                    WHERE al.ano = ?
                """, (ano_visualizado,))
                total_provas = cursor.fetchone()["total"]

        # =====================================================
        # USUÁRIOS VINCULADOS A UMA INSTITUIÇÃO
        # Usa o ID exato do ano selecionado para aquela escola.
        # =====================================================

        elif escola_id:
            cursor.execute("""
                SELECT nome_instituicao
                FROM escolas
                WHERE id = ?
                LIMIT 1
            """, (escola_id,))

            escola = cursor.fetchone()
            if escola:
                nome_instituicao = escola["nome_instituicao"]

            total_instituicoes = 1

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM usuarios
                WHERE escola_id = ?
                  AND ativo = 1
            """, (escola_id,))
            total_usuarios = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM professores
                WHERE escola_id = ?
            """, (escola_id,))
            total_professores = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM questoes
                WHERE escola_id = ?
            """, (escola_id,))
            total_questoes = cursor.fetchone()["total"]

            if ano_letivo_id:
                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM turmas
                    WHERE escola_id = ?
                      AND ano_letivo_id = ?
                """, (escola_id, ano_letivo_id))
                total_turmas = cursor.fetchone()["total"]

                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM (
                        SELECT aluno_id
                        FROM aluno_matriculas
                        WHERE escola_id = ?
                          AND ano_letivo_id = ?

                        UNION

                        SELECT id AS aluno_id
                        FROM alunos
                        WHERE escola_id = ?
                          AND ano_letivo_id = ?
                    ) AS alunos_do_ano
                """, (
                    escola_id,
                    ano_letivo_id,
                    escola_id,
                    ano_letivo_id
                ))
                total_alunos = cursor.fetchone()["total"]

                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM provas
                    WHERE escola_id = ?
                      AND ano_letivo_id = ?
                """, (escola_id, ano_letivo_id))
                total_provas = cursor.fetchone()["total"]

        else:
            nome_instituicao = "Usuário sem instituição vinculada"

        return render_template(
            "dashboard/index.html",
            total_instituicoes=total_instituicoes,
            total_usuarios=total_usuarios,
            total_professores=total_professores,
            total_alunos=total_alunos,
            total_turmas=total_turmas,
            total_questoes=total_questoes,
            total_provas=total_provas,
            nome_instituicao=nome_instituicao,
            ano_letivo_id_ativo=ano_letivo_id_ativo,
            ano_letivo_ativo=ano_letivo_ativo,
            ano_letivo_id_visualizado=ano_letivo_id,
            ano_letivo_visualizado=ano_visualizado,
            consultando_historico=contexto["consultando_historico"],
            permissoes_usuario=permissoes_usuario
        )

    except sqlite3.Error as erro:
        import traceback
        traceback.print_exc()

        print("ERRO AO CARREGAR O DASHBOARD:", erro)
        flash(
            f"Erro ao carregar os dados do dashboard: {erro}",
            "erro"
        )

        return render_template(
            "dashboard/index.html",
            total_instituicoes=0,
            total_usuarios=0,
            total_professores=0,
            total_alunos=0,
            total_turmas=0,
            total_questoes=0,
            total_provas=0,
            nome_instituicao=nome_instituicao,
            ano_letivo_id_ativo=ano_letivo_id_ativo,
            ano_letivo_ativo=ano_letivo_ativo,
            ano_letivo_id_visualizado=ano_letivo_id,
            ano_letivo_visualizado=ano_visualizado,
            consultando_historico=contexto["consultando_historico"],
            permissoes_usuario=permissoes_usuario
        )

    finally:
        banco.close()


@app.route("/esqueci_senha")
def esqueci_senha():
    return render_template("esqueci_senha.html")

# =========================================================
# LISTAR TURMAS PELO ANO LETIVO SELECIONADO
# =========================================================

@app.route("/turmas")
def turmas():

    if not permissao_modulo("Turmas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = session.get("usuario_cargo", "").strip()
    usuario_id = session.get("usuario_id")
    escola_id = obter_escola_usuario()

    pode_gerenciar = cargo in [
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]

    escolas = []
    lista_turmas = []

    ano_letivo_visualizado = session.get("ano_letivo_visualizado")
    ano_letivo_id = session.get("ano_letivo_id")
    consultando_ano_antigo = False

    try:

        # =====================================================
        # ADMINISTRADOR GERAL
        #
        # Para ele, o mesmo número de ano é aplicado a todas
        # as instituições. Cada escola possui um ID diferente
        # para o seu próprio registro em anos_letivos.
        # =====================================================

        if cargo == "Administrador Geral":

            if not ano_letivo_visualizado:
                ano_letivo_visualizado = datetime.now().year
                session["ano_letivo_visualizado"] = ano_letivo_visualizado
                session["ano_letivo"] = ano_letivo_visualizado

            try:
                ano_letivo_visualizado = int(ano_letivo_visualizado)
            except (TypeError, ValueError):
                ano_letivo_visualizado = datetime.now().year
                session["ano_letivo_visualizado"] = ano_letivo_visualizado
                session["ano_letivo"] = ano_letivo_visualizado

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM anos_letivos
                WHERE ano = ?
                  AND ativo = 1
                  AND encerrado = 0
            """, (
                ano_letivo_visualizado,
            ))

            resultado_ativo = cursor.fetchone()

            consultando_ano_antigo = (
                not resultado_ativo
                or resultado_ativo["total"] == 0
            )

            cursor.execute("""
                SELECT
                    t.*,
                    e.nome_instituicao,

                    al.id AS ano_letivo_id_atual,
                    al.ano AS ano_letivo_atual,
                    al.ativo AS ano_letivo_ativo,
                    al.encerrado AS ano_letivo_encerrado,

                    CASE
                        WHEN (
                            SELECT COUNT(*)
                            FROM aluno_matriculas AS am
                            WHERE am.turma_id = t.id
                              AND am.ano_letivo_id = t.ano_letivo_id
                        ) > 0
                        THEN (
                            SELECT COUNT(*)
                            FROM aluno_matriculas AS am
                            WHERE am.turma_id = t.id
                              AND am.ano_letivo_id = t.ano_letivo_id
                        )
                        ELSE (
                            SELECT COUNT(*)
                            FROM alunos AS a
                            WHERE a.turma_id = t.id
                              AND a.ano_letivo_id = t.ano_letivo_id
                        )
                    END AS total_alunos,

                    (
                        SELECT COUNT(DISTINCT pv.professor_id)
                        FROM professor_vinculos AS pv
                        WHERE pv.turma_id = t.id
                    ) AS total_professores

                FROM turmas AS t

                INNER JOIN anos_letivos AS al
                    ON al.id = t.ano_letivo_id
                   AND al.escola_id = t.escola_id

                INNER JOIN escolas AS e
                    ON e.id = t.escola_id

                WHERE al.ano = ?

                ORDER BY
                    e.nome_instituicao COLLATE NOCASE ASC,
                    t.etapa COLLATE NOCASE ASC,
                    t.ano COLLATE NOCASE ASC,
                    t.nome COLLATE NOCASE ASC,
                    t.turno COLLATE NOCASE ASC
            """, (
                ano_letivo_visualizado,
            ))

            lista_turmas = cursor.fetchall()

            # Todas as instituições ativas devem aparecer no cadastro.
            # O LEFT JOIN mantém também as instituições que ainda não
            # possuem ano letivo ativo configurado.
            cursor.execute("""
                SELECT
                    e.id,
                    e.nome_instituicao,
                    al.id AS ano_letivo_id,
                    al.ano AS ano_letivo_ativo

                FROM escolas AS e

                LEFT JOIN anos_letivos AS al
                    ON al.escola_id = e.id
                   AND al.ativo = 1
                   AND al.encerrado = 0

                WHERE COALESCE(e.status, 1) = 1

                ORDER BY
                    e.nome_instituicao COLLATE NOCASE ASC
            """)

            escolas = cursor.fetchall()

        # =====================================================
        # USUÁRIOS VINCULADOS A UMA INSTITUIÇÃO
        # =====================================================

        else:

            if not escola_id:
                flash(
                    "Não foi possível identificar sua instituição.",
                    "erro"
                )

                return render_template(
                    "gestao/turmas.html",
                    turmas=[],
                    escolas=[],
                    cargo=cargo,
                    pode_gerenciar=pode_gerenciar,
                    ano_letivo_ativo=None,
                    ano_letivo_visualizado=None,
                    consultando_ano_antigo=False
                )

            # Recupera o ano selecionado ou, por padrão, o ativo.
            ano_selecionado = atualizar_ano_letivo_na_sessao(escola_id)

            if not ano_selecionado:
                flash(
                    "A instituição não possui um ano letivo disponível.",
                    "erro"
                )

                return render_template(
                    "gestao/turmas.html",
                    turmas=[],
                    escolas=[],
                    cargo=cargo,
                    pode_gerenciar=pode_gerenciar,
                    ano_letivo_ativo=None,
                    ano_letivo_visualizado=None,
                    consultando_ano_antigo=False
                )

            ano_letivo_id = ano_selecionado["id"]
            ano_letivo_visualizado = ano_selecionado["ano"]

            session["ano_letivo_id"] = ano_letivo_id
            session["ano_letivo"] = ano_letivo_visualizado
            session["ano_letivo_visualizado"] = ano_letivo_visualizado

            consultando_ano_antigo = not (
                ano_selecionado["ativo"] == 1
                and ano_selecionado["encerrado"] == 0
            )

            # Professor vê apenas turmas em que possui vínculo.
            if cargo == "Professor":

                cursor.execute("""
                    SELECT DISTINCT
                        t.*,
                        e.nome_instituicao,

                        al.id AS ano_letivo_id_atual,
                        al.ano AS ano_letivo_atual,
                        al.ativo AS ano_letivo_ativo,
                        al.encerrado AS ano_letivo_encerrado,

                        CASE
                            WHEN (
                                SELECT COUNT(*)
                                FROM aluno_matriculas AS am
                                WHERE am.turma_id = t.id
                                  AND am.ano_letivo_id = t.ano_letivo_id
                            ) > 0
                            THEN (
                                SELECT COUNT(*)
                                FROM aluno_matriculas AS am
                                WHERE am.turma_id = t.id
                                  AND am.ano_letivo_id = t.ano_letivo_id
                            )
                            ELSE (
                                SELECT COUNT(*)
                                FROM alunos AS a
                                WHERE a.turma_id = t.id
                                  AND a.ano_letivo_id = t.ano_letivo_id
                            )
                        END AS total_alunos,

                        (
                            SELECT COUNT(DISTINCT pv_total.professor_id)
                            FROM professor_vinculos AS pv_total
                            WHERE pv_total.turma_id = t.id
                        ) AS total_professores

                    FROM turmas AS t

                    INNER JOIN professor_vinculos AS pv
                        ON pv.turma_id = t.id

                    INNER JOIN anos_letivos AS al
                        ON al.id = t.ano_letivo_id
                       AND al.escola_id = t.escola_id

                    INNER JOIN escolas AS e
                        ON e.id = t.escola_id

                    WHERE pv.professor_id = ?
                      AND t.escola_id = ?
                      AND t.ano_letivo_id = ?

                    ORDER BY
                        t.etapa COLLATE NOCASE ASC,
                        t.ano COLLATE NOCASE ASC,
                        t.nome COLLATE NOCASE ASC,
                        t.turno COLLATE NOCASE ASC
                """, (
                    usuario_id,
                    escola_id,
                    ano_letivo_id
                ))

            else:

                cursor.execute("""
                    SELECT
                        t.*,
                        e.nome_instituicao,

                        al.id AS ano_letivo_id_atual,
                        al.ano AS ano_letivo_atual,
                        al.ativo AS ano_letivo_ativo,
                        al.encerrado AS ano_letivo_encerrado,

                        CASE
                            WHEN (
                                SELECT COUNT(*)
                                FROM aluno_matriculas AS am
                                WHERE am.turma_id = t.id
                                  AND am.ano_letivo_id = t.ano_letivo_id
                            ) > 0
                            THEN (
                                SELECT COUNT(*)
                                FROM aluno_matriculas AS am
                                WHERE am.turma_id = t.id
                                  AND am.ano_letivo_id = t.ano_letivo_id
                            )
                            ELSE (
                                SELECT COUNT(*)
                                FROM alunos AS a
                                WHERE a.turma_id = t.id
                                  AND a.ano_letivo_id = t.ano_letivo_id
                            )
                        END AS total_alunos,

                        (
                            SELECT COUNT(DISTINCT pv.professor_id)
                            FROM professor_vinculos AS pv
                            WHERE pv.turma_id = t.id
                        ) AS total_professores

                    FROM turmas AS t

                    INNER JOIN anos_letivos AS al
                        ON al.id = t.ano_letivo_id
                       AND al.escola_id = t.escola_id

                    INNER JOIN escolas AS e
                        ON e.id = t.escola_id

                    WHERE t.escola_id = ?
                      AND t.ano_letivo_id = ?

                    ORDER BY
                        t.etapa COLLATE NOCASE ASC,
                        t.ano COLLATE NOCASE ASC,
                        t.nome COLLATE NOCASE ASC,
                        t.turno COLLATE NOCASE ASC
                """, (
                    escola_id,
                    ano_letivo_id
                ))

            lista_turmas = cursor.fetchall()

        return render_template(
            "gestao/turmas.html",
            turmas=lista_turmas,
            escolas=escolas,
            cargo=cargo,
            pode_gerenciar=pode_gerenciar,

            # Mantido para compatibilidade com seu HTML atual.
            ano_letivo_ativo=ano_letivo_visualizado,

            # Novos nomes.
            ano_letivo_visualizado=ano_letivo_visualizado,
            consultando_ano_antigo=consultando_ano_antigo
        )

    except sqlite3.Error as erro:

        import traceback
        traceback.print_exc()

        print("ERRO AO LISTAR TURMAS:", erro)

        flash(
            f"Erro ao carregar as turmas: {erro}",
            "erro"
        )

        return render_template(
            "gestao/turmas.html",
            turmas=[],
            escolas=[],
            cargo=cargo,
            pode_gerenciar=pode_gerenciar,
            ano_letivo_ativo=None,
            ano_letivo_visualizado=None,
            consultando_ano_antigo=False
        )

    finally:
        banco.close()


# =========================================================
# CADASTRAR TURMA NO ANO LETIVO VISUALIZADO
# =========================================================

@app.route("/cadastrar_turma", methods=["POST"])
def cadastrar_turma():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):

        flash(
            "Você não possui permissão para cadastrar turmas.",
            "erro"
        )

        return redirect("/acesso_negado")

    etapa = request.form.get("etapa", "").strip()
    ano_serie = request.form.get("ano", "").strip()
    identificacao = request.form.get("nome", "").strip()
    turno = request.form.get("turno", "").strip()

    cargo = session.get("usuario_cargo", "").strip()
    escola_id = obter_escola_usuario()

    if not etapa:
        flash("Selecione a etapa de ensino.", "erro")
        return redirect("/turmas")

    if not ano_serie:
        flash("Selecione o ano ou série.", "erro")
        return redirect("/turmas")

    if not identificacao:
        flash("Informe a identificação da turma.", "erro")
        return redirect("/turmas")

    if not turno:
        flash("Selecione o turno.", "erro")
        return redirect("/turmas")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        # =====================================================
        # DESCOBRIR A INSTITUIÇÃO
        # =====================================================

        if cargo == "Administrador Geral":

            escola_formulario = request.form.get(
                "escola_id",
                ""
            ).strip()

            if not escola_formulario:
                flash("Selecione uma instituição.", "erro")
                return redirect("/turmas")

            try:
                escola_id = int(escola_formulario)
            except (TypeError, ValueError):
                flash(
                    "A instituição selecionada é inválida.",
                    "erro"
                )
                return redirect("/turmas")

        elif not escola_id:

            flash(
                "Não foi possível identificar a instituição da turma.",
                "erro"
            )

            return redirect("/turmas")

        # =====================================================
        # VALIDAR INSTITUIÇÃO
        # =====================================================

        cursor.execute("""
            SELECT
                id,
                nome_instituicao
            FROM escolas
            WHERE id = ?
              AND COALESCE(status, 1) = 1
            LIMIT 1
        """, (
            escola_id,
        ))

        escola = cursor.fetchone()

        if escola is None:

            flash(
                "A instituição selecionada não existe ou está inativa.",
                "erro"
            )

            return redirect("/turmas")

        # =====================================================
        # DEFINIR O ANO EM QUE A TURMA SERÁ CRIADA
        # =====================================================

        if cargo == "Administrador Geral":

            numero_ano_letivo = session.get(
                "ano_letivo_visualizado"
            ) or session.get("ano_letivo")

            if not numero_ano_letivo:
                flash(
                    "Selecione um ano letivo no topo da plataforma.",
                    "erro"
                )
                return redirect("/turmas")

            try:
                numero_ano_letivo = int(numero_ano_letivo)
            except (TypeError, ValueError):
                flash("O ano letivo selecionado é inválido.", "erro")
                return redirect("/turmas")

            cursor.execute("""
                SELECT
                    id,
                    ano,
                    ativo,
                    encerrado
                FROM anos_letivos
                WHERE escola_id = ?
                  AND ano = ?
                LIMIT 1
            """, (
                escola_id,
                numero_ano_letivo
            ))

            ano_letivo = cursor.fetchone()

        else:

            ano_letivo = atualizar_ano_letivo_na_sessao(
                escola_id
            )

        if ano_letivo is None:

            flash(
                "A instituição não possui o ano letivo "
                "selecionado. Cadastre ou prepare esse ano antes "
                "de criar uma turma.",
                "erro"
            )

            return redirect("/turmas")

        ano_letivo_id = ano_letivo["id"]
        numero_ano_letivo = ano_letivo["ano"]

        # Impede alterações acidentais em anos encerrados.
        if (
            ano_letivo["encerrado"] == 1
            or ano_letivo["ativo"] != 1
        ):

            flash(
                f"O ano letivo {numero_ano_letivo} está em modo "
                "de consulta. Volte ao ano ativo para cadastrar turmas.",
                "erro"
            )

            return redirect("/turmas")

        # =====================================================
        # VERIFICAR TURMA DUPLICADA NO MESMO ANO
        # =====================================================

        cursor.execute("""
            SELECT id
            FROM turmas
            WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?))
              AND LOWER(TRIM(etapa)) = LOWER(TRIM(?))
              AND LOWER(TRIM(ano)) = LOWER(TRIM(?))
              AND LOWER(TRIM(turno)) = LOWER(TRIM(?))
              AND escola_id = ?
              AND ano_letivo_id = ?
            LIMIT 1
        """, (
            identificacao,
            etapa,
            ano_serie,
            turno,
            escola_id,
            ano_letivo_id
        ))

        if cursor.fetchone():

            flash(
                f"Já existe uma turma igual cadastrada em "
                f"{numero_ano_letivo}.",
                "erro"
            )

            return redirect("/turmas")

        # =====================================================
        # CADASTRAR TURMA
        # =====================================================

        cursor.execute("""
            INSERT INTO turmas (
                nome,
                etapa,
                ano,
                turno,
                escola_id,
                ano_letivo,
                ano_letivo_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            identificacao,
            etapa,
            ano_serie,
            turno,
            escola_id,
            numero_ano_letivo,
            ano_letivo_id
        ))

        banco.commit()

        flash(
            f"Turma cadastrada com sucesso no ano letivo "
            f"{numero_ano_letivo}.",
            "success"
        )

        return redirect("/turmas")

    except sqlite3.Error as erro:

        banco.rollback()

        import traceback
        traceback.print_exc()

        print(
            "ERRO DO BANCO AO CADASTRAR TURMA:",
            erro
        )

        flash(
            f"Erro ao cadastrar turma: {erro}",
            "erro"
        )

        return redirect("/turmas")

    finally:
        banco.close()

# =========================================================
# VISUALIZAR TURMA
# =========================================================

@app.route("/turmas/<int:turma_id>")
def visualizar_turma(turma_id):

    if not permissao_modulo("Turmas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = session.get("usuario_cargo", "").strip()
    usuario_id = session.get("usuario_id")
    escola_id = session.get("escola_id")

    pode_editar = cargo in [
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]

    try:

        cursor.execute("""
            SELECT
                turmas.*,
                escolas.nome_instituicao
            FROM turmas
            LEFT JOIN escolas
                ON escolas.id = turmas.escola_id
            WHERE turmas.id = ?
            LIMIT 1
        """, (
            turma_id,
        ))

        turma = cursor.fetchone()

        if turma is None:

            flash(
                "Turma não encontrada.",
                "erro"
            )

            return redirect("/turmas")

        # Professor só acessa turma em que possui vínculo.
        if cargo == "Professor":

            cursor.execute("""
                SELECT 1
                FROM professor_vinculos
                WHERE professor_id = ?
                  AND turma_id = ?
                LIMIT 1
            """, (
                usuario_id,
                turma_id
            ))

            if cursor.fetchone() is None:

                flash(
                    "Você não está vinculado a esta turma.",
                    "erro"
                )

                return redirect("/turmas")

        elif cargo != "Administrador Geral":

            if turma["escola_id"] != escola_id:

                flash(
                    "Você não possui acesso a esta turma.",
                    "erro"
                )

                return redirect("/turmas")

        cursor.execute("""
            SELECT *
            FROM alunos
            WHERE turma_id = ?
            ORDER BY nome COLLATE NOCASE ASC
        """, (
            turma_id,
        ))

        alunos = cursor.fetchall()

        cursor.execute("""
            SELECT
                usuarios.id,
                usuarios.nome,
                usuarios.email,
                cargos.nome AS cargo,
                GROUP_CONCAT(
                    DISTINCT componentes_curriculares.nome
                ) AS componentes

            FROM professor_vinculos

            INNER JOIN usuarios
                ON usuarios.id =
                   professor_vinculos.professor_id

            INNER JOIN cargos
                ON cargos.id = usuarios.cargo_id

            LEFT JOIN componentes_curriculares
                ON componentes_curriculares.id =
                   professor_vinculos.componente_id

            WHERE professor_vinculos.turma_id = ?

            GROUP BY
                usuarios.id,
                usuarios.nome,
                usuarios.email,
                cargos.nome

            ORDER BY usuarios.nome COLLATE NOCASE ASC
        """, (
            turma_id,
        ))

        professores = cursor.fetchall()

        cursor.execute("""
            SELECT *
            FROM provas
            WHERE turma_id = ?
            ORDER BY id DESC
        """, (
            turma_id,
        ))

        avaliacoes = cursor.fetchall()

        return render_template(
            "gestao/visualizar_turma.html",
            turma=turma,
            alunos=alunos,
            professores=professores,
            avaliacoes=avaliacoes,
            cargo=cargo,
            pode_editar=pode_editar
        )

    except sqlite3.Error as erro:

        import traceback
        traceback.print_exc()

        print("ERRO AO VISUALIZAR TURMA:", erro)

        flash(
            f"Erro ao carregar os dados da turma: {erro}",
            "erro"
        )

        return redirect("/turmas")

    finally:
        banco.close()


# =========================================================
# EDITAR TURMA
# =========================================================

@app.route(
    "/turmas/<int:turma_id>/editar",
    methods=["GET", "POST"]
)
def editar_turma(turma_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):

        flash(
            "Você não possui permissão para editar turmas.",
            "erro"
        )

        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = session.get("usuario_cargo", "").strip()
    escola_id = session.get("escola_id")

    try:

        cursor.execute("""
            SELECT *
            FROM turmas
            WHERE id = ?
            LIMIT 1
        """, (
            turma_id,
        ))

        turma = cursor.fetchone()

        if turma is None:

            flash(
                "Turma não encontrada.",
                "erro"
            )

            return redirect("/turmas")

        if (
            cargo != "Administrador Geral"
            and turma["escola_id"] != escola_id
        ):

            flash(
                "Você não possui acesso a esta turma.",
                "erro"
            )

            return redirect("/turmas")

        if request.method == "POST":

            etapa = request.form.get("etapa", "").strip()
            ano = request.form.get("ano", "").strip()
            nome = request.form.get("nome", "").strip()
            turno = request.form.get("turno", "").strip()

            if not etapa or not ano or not nome or not turno:

                flash(
                    "Preencha todos os dados da turma.",
                    "erro"
                )

                return redirect(
                    f"/turmas/{turma_id}/editar"
                )

            cursor.execute("""
                SELECT id
                FROM turmas
                WHERE LOWER(TRIM(nome)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(etapa)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(ano)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(turno)) = LOWER(TRIM(?))
                  AND escola_id = ?
                  AND id != ?
                LIMIT 1
            """, (
                nome,
                etapa,
                ano,
                turno,
                turma["escola_id"],
                turma_id
            ))

            if cursor.fetchone():

                flash(
                    "Já existe outra turma com esses mesmos dados.",
                    "erro"
                )

                return redirect(
                    f"/turmas/{turma_id}/editar"
                )

            cursor.execute("""
                UPDATE turmas
                SET
                    nome = ?,
                    etapa = ?,
                    ano = ?,
                    turno = ?
                WHERE id = ?
            """, (
                nome,
                etapa,
                ano,
                turno,
                turma_id
            ))

            banco.commit()

            flash(
                "Turma atualizada com sucesso.",
                "success"
            )

            return redirect(
                f"/turmas/{turma_id}"
            )

        return render_template(
            "gestao/editar_turma.html",
            turma=turma
        )

    except sqlite3.Error as erro:

        banco.rollback()

        import traceback
        traceback.print_exc()

        print("ERRO AO EDITAR TURMA:", erro)

        flash(
            f"Erro ao editar turma: {erro}",
            "erro"
        )

        return redirect("/turmas")

    finally:
        banco.close()


# =========================================================
# EXCLUIR TURMA
# =========================================================

@app.route(
    "/turmas/<int:turma_id>/excluir",
    methods=["POST"]
)
def excluir_turma(turma_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):

        flash(
            "Você não possui permissão para excluir turmas.",
            "erro"
        )

        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = session.get("usuario_cargo", "").strip()
    escola_id = session.get("escola_id")

    try:

        cursor.execute("""
            SELECT *
            FROM turmas
            WHERE id = ?
            LIMIT 1
        """, (
            turma_id,
        ))

        turma = cursor.fetchone()

        if turma is None:

            flash(
                "Turma não encontrada.",
                "erro"
            )

            return redirect("/turmas")

        if (
            cargo != "Administrador Geral"
            and turma["escola_id"] != escola_id
        ):

            flash(
                "Você não possui permissão para excluir esta turma.",
                "erro"
            )

            return redirect("/turmas")

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM alunos
            WHERE turma_id = ?
        """, (
            turma_id,
        ))

        total_alunos = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM provas
            WHERE turma_id = ?
        """, (
            turma_id,
        ))

        total_provas = cursor.fetchone()["total"]

        if total_alunos > 0 or total_provas > 0:

            flash(
                "Não é possível excluir a turma porque existem alunos ou avaliações vinculados.",
                "erro"
            )

            return redirect("/turmas")

        cursor.execute("""
            DELETE FROM professor_vinculos
            WHERE turma_id = ?
        """, (
            turma_id,
        ))

        cursor.execute("""
            DELETE FROM professor_turmas
            WHERE turma_id = ?
        """, (
            turma_id,
        ))

        cursor.execute("""
            DELETE FROM coordenador_turmas
            WHERE turma_id = ?
        """, (
            turma_id,
        ))

        cursor.execute("""
            DELETE FROM turmas
            WHERE id = ?
        """, (
            turma_id,
        ))

        banco.commit()

        flash(
            "Turma excluída com sucesso.",
            "success"
        )

        return redirect("/turmas")

    except sqlite3.Error as erro:

        banco.rollback()

        import traceback
        traceback.print_exc()

        print("ERRO AO EXCLUIR TURMA:", erro)

        flash(
            f"Erro ao excluir turma: {erro}",
            "erro"
        )

        return redirect("/turmas")

    finally:
        banco.close()

# =========================================================
# LISTAR ALUNOS E MATRÍCULAS PELO ANO SELECIONADO
# =========================================================

@app.route("/alunos")
def alunos():
    """Lista alunos e prepara o formulário de matrícula por instituição."""

    if not permissao_modulo("Alunos"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    contexto = obter_contexto_plataforma()
    cargo = contexto["cargo"]
    escola_id = contexto["escola_id"]
    ano_letivo_id = contexto["ano_letivo_id"]
    ano_visualizado = contexto["ano"]
    consultando_historico = contexto["consultando_historico"]

    lista_escolas = []
    lista_turmas = []
    lista_alunos = []

    def renderizar(**extras):
        dados = {
            "alunos": lista_alunos,
            "turmas": lista_turmas,
            "escolas": lista_escolas,
            "cargo": cargo,
            "escola_id_usuario": escola_id,
            "ano_letivo_ativo": ano_visualizado,
            "ano_letivo_visualizado": ano_visualizado,
            "consultando_ano_antigo": consultando_historico,
        }
        dados.update(extras)
        return render_template("alunos.html", **dados)

    try:
        if cargo == "Administrador Geral":
            if ano_visualizado is None:
                ano_visualizado = obter_ano_global_administrador()

            if ano_visualizado is None:
                return renderizar(
                    ano_letivo_ativo=None,
                    ano_letivo_visualizado=None,
                    consultando_ano_antigo=False,
                )

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM anos_letivos
                WHERE ano = ? AND ativo = 1 AND encerrado = 0
            """, (ano_visualizado,))
            resultado_ativo = cursor.fetchone()
            consultando_historico = not resultado_ativo or resultado_ativo["total"] == 0

            # Instituições disponíveis no ano selecionado.
            cursor.execute("""
                SELECT DISTINCT e.id, e.nome_instituicao
                FROM escolas AS e
                INNER JOIN anos_letivos AS al
                    ON al.escola_id = e.id
                   AND al.ano = ?
                WHERE COALESCE(e.status, 1) = 1
                ORDER BY e.nome_instituicao COLLATE NOCASE ASC
            """, (ano_visualizado,))
            lista_escolas = cursor.fetchall()

            # Todas as turmas são enviadas ao template com escola_id.
            # O JavaScript da página exibe somente as da instituição escolhida.
            cursor.execute("""
                SELECT
                    t.id, t.nome, t.etapa, t.ano, t.turno,
                    t.escola_id, t.ano_letivo_id,
                    e.nome_instituicao,
                    al.ano AS ano_letivo
                FROM turmas AS t
                INNER JOIN anos_letivos AS al
                    ON al.id = t.ano_letivo_id
                   AND al.escola_id = t.escola_id
                INNER JOIN escolas AS e ON e.id = t.escola_id
                WHERE al.ano = ?
                ORDER BY
                    e.nome_instituicao COLLATE NOCASE ASC,
                    t.etapa COLLATE NOCASE ASC,
                    t.ano COLLATE NOCASE ASC,
                    t.nome COLLATE NOCASE ASC,
                    t.turno COLLATE NOCASE ASC
            """, (ano_visualizado,))
            lista_turmas = cursor.fetchall()

            cursor.execute("""
                SELECT * FROM (
                    SELECT
                        a.id, a.nome, a.matricula,
                        am.turma_id, am.escola_id, am.ano_letivo_id,
                        am.id AS matricula_id, am.situacao,
                        t.nome AS nome_turma, t.ano AS ano_turma,
                        t.etapa, t.turno,
                        e.nome_instituicao, al.ano AS ano_letivo
                    FROM aluno_matriculas AS am
                    INNER JOIN alunos AS a ON a.id = am.aluno_id
                    INNER JOIN turmas AS t
                        ON t.id = am.turma_id
                       AND t.escola_id = am.escola_id
                       AND t.ano_letivo_id = am.ano_letivo_id
                    INNER JOIN anos_letivos AS al
                        ON al.id = am.ano_letivo_id
                       AND al.escola_id = am.escola_id
                    INNER JOIN escolas AS e ON e.id = am.escola_id
                    WHERE al.ano = ?

                    UNION ALL

                    SELECT
                        a.id, a.nome, a.matricula,
                        a.turma_id, a.escola_id, a.ano_letivo_id,
                        NULL AS matricula_id, 'Cursando' AS situacao,
                        t.nome AS nome_turma, t.ano AS ano_turma,
                        t.etapa, t.turno,
                        e.nome_instituicao, al.ano AS ano_letivo
                    FROM alunos AS a
                    INNER JOIN turmas AS t
                        ON t.id = a.turma_id
                       AND t.escola_id = a.escola_id
                       AND t.ano_letivo_id = a.ano_letivo_id
                    INNER JOIN anos_letivos AS al
                        ON al.id = a.ano_letivo_id
                       AND al.escola_id = a.escola_id
                    INNER JOIN escolas AS e ON e.id = a.escola_id
                    WHERE al.ano = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM aluno_matriculas AS am
                          WHERE am.aluno_id = a.id
                            AND am.ano_letivo_id = a.ano_letivo_id
                      )
                ) AS alunos_ano
                ORDER BY nome_instituicao COLLATE NOCASE ASC, nome COLLATE NOCASE ASC
            """, (ano_visualizado, ano_visualizado))
            lista_alunos = cursor.fetchall()

        else:
            if not escola_id:
                flash("Não foi possível identificar sua instituição.", "erro")
                return renderizar(
                    ano_letivo_ativo=None,
                    ano_letivo_visualizado=None,
                    consultando_ano_antigo=False,
                )

            ano_selecionado = atualizar_ano_letivo_na_sessao(escola_id)
            if not ano_selecionado:
                flash("A instituição não possui um ano letivo disponível.", "erro")
                return renderizar(
                    ano_letivo_ativo=None,
                    ano_letivo_visualizado=None,
                    consultando_ano_antigo=False,
                )

            ano_letivo_id = ano_selecionado["id"]
            ano_visualizado = ano_selecionado["ano"]
            consultando_historico = not (
                ano_selecionado["ativo"] == 1 and ano_selecionado["encerrado"] == 0
            )

            session["ano_letivo_id"] = ano_letivo_id
            session["ano_letivo"] = ano_visualizado
            session["ano_letivo_visualizado"] = ano_visualizado

            # Para usuários institucionais, somente a própria instituição.
            cursor.execute("""
                SELECT id, nome_instituicao
                FROM escolas
                WHERE id = ? AND COALESCE(status, 1) = 1
                LIMIT 1
            """, (escola_id,))
            escola_usuario = cursor.fetchone()
            lista_escolas = [escola_usuario] if escola_usuario else []

            cursor.execute("""
                SELECT
                    t.id, t.nome, t.etapa, t.ano, t.turno,
                    t.escola_id, t.ano_letivo_id,
                    e.nome_instituicao,
                    al.ano AS ano_letivo
                FROM turmas AS t
                INNER JOIN anos_letivos AS al
                    ON al.id = t.ano_letivo_id
                   AND al.escola_id = t.escola_id
                INNER JOIN escolas AS e ON e.id = t.escola_id
                WHERE t.escola_id = ? AND t.ano_letivo_id = ?
                ORDER BY
                    t.etapa COLLATE NOCASE ASC,
                    t.ano COLLATE NOCASE ASC,
                    t.nome COLLATE NOCASE ASC,
                    t.turno COLLATE NOCASE ASC
            """, (escola_id, ano_letivo_id))
            lista_turmas = cursor.fetchall()

            cursor.execute("""
                SELECT * FROM (
                    SELECT
                        a.id, a.nome, a.matricula,
                        am.turma_id, am.escola_id, am.ano_letivo_id,
                        am.id AS matricula_id, am.situacao,
                        t.nome AS nome_turma, t.ano AS ano_turma,
                        t.etapa, t.turno,
                        e.nome_instituicao, al.ano AS ano_letivo
                    FROM aluno_matriculas AS am
                    INNER JOIN alunos AS a ON a.id = am.aluno_id
                    INNER JOIN turmas AS t
                        ON t.id = am.turma_id
                       AND t.escola_id = am.escola_id
                       AND t.ano_letivo_id = am.ano_letivo_id
                    INNER JOIN anos_letivos AS al
                        ON al.id = am.ano_letivo_id
                       AND al.escola_id = am.escola_id
                    INNER JOIN escolas AS e ON e.id = am.escola_id
                    WHERE am.escola_id = ? AND am.ano_letivo_id = ?

                    UNION ALL

                    SELECT
                        a.id, a.nome, a.matricula,
                        a.turma_id, a.escola_id, a.ano_letivo_id,
                        NULL AS matricula_id, 'Cursando' AS situacao,
                        t.nome AS nome_turma, t.ano AS ano_turma,
                        t.etapa, t.turno,
                        e.nome_instituicao, al.ano AS ano_letivo
                    FROM alunos AS a
                    INNER JOIN turmas AS t
                        ON t.id = a.turma_id
                       AND t.escola_id = a.escola_id
                       AND t.ano_letivo_id = a.ano_letivo_id
                    INNER JOIN anos_letivos AS al
                        ON al.id = a.ano_letivo_id
                       AND al.escola_id = a.escola_id
                    INNER JOIN escolas AS e ON e.id = a.escola_id
                    WHERE a.escola_id = ? AND a.ano_letivo_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM aluno_matriculas AS am
                          WHERE am.aluno_id = a.id
                            AND am.ano_letivo_id = a.ano_letivo_id
                      )
                ) AS alunos_ano
                ORDER BY nome COLLATE NOCASE ASC
            """, (escola_id, ano_letivo_id, escola_id, ano_letivo_id))
            lista_alunos = cursor.fetchall()

        return renderizar(
            ano_letivo_ativo=ano_visualizado,
            ano_letivo_visualizado=ano_visualizado,
            consultando_ano_antigo=consultando_historico,
        )

    except sqlite3.Error as erro:
        import traceback
        traceback.print_exc()
        print("ERRO AO LISTAR ALUNOS:", erro)
        flash(f"Erro ao carregar os alunos: {erro}", "erro")
        lista_escolas = []
        lista_turmas = []
        lista_alunos = []
        return renderizar()

    finally:
        banco.close()


# =========================================================
# GERAR NÚMERO DE MATRÍCULA AUTOMATICAMENTE
# =========================================================

def gerar_numero_matricula(cursor, escola_id, numero_ano):
    """
    Gera uma matrícula no formato AAAA + sequência de 4 dígitos.

    Exemplo:
        20260001
        20260002

    A sequência é independente para cada instituição e ano letivo.
    """

    prefixo = str(numero_ano)

    cursor.execute("""
        SELECT matricula
        FROM alunos
        WHERE escola_id = ?
          AND matricula LIKE ?
        ORDER BY id ASC
    """, (
        escola_id,
        f"{prefixo}%"
    ))

    maior_sequencia = 0

    for registro in cursor.fetchall():
        matricula_existente = str(registro["matricula"] or "").strip()

        if not matricula_existente.startswith(prefixo):
            continue

        sufixo = matricula_existente[len(prefixo):]

        if sufixo.isdigit():
            maior_sequencia = max(
                maior_sequencia,
                int(sufixo)
            )

    proxima_sequencia = maior_sequencia + 1

    while True:
        matricula = f"{prefixo}{proxima_sequencia:04d}"

        cursor.execute("""
            SELECT id
            FROM alunos
            WHERE escola_id = ?
              AND matricula = ?
            LIMIT 1
        """, (
            escola_id,
            matricula
        ))

        if cursor.fetchone() is None:
            return matricula

        proxima_sequencia += 1


# =========================================================
# CADASTRAR ALUNO E CRIAR MATRÍCULA ANUAL
# =========================================================

@app.route("/cadastrar_aluno", methods=["POST"])
def cadastrar_aluno():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/acesso_negado")

    nome = request.form.get("nome", "").strip()
    modo_matricula = request.form.get(
        "modo_matricula",
        "automatica"
    ).strip().lower()
    matricula = request.form.get("matricula", "").strip()
    turma_id = request.form.get("turma_id", "").strip()
    escola_id_form = request.form.get("escola_id", "").strip()

    cargo = session.get("usuario_cargo", "").strip()
    escola_id_usuario = obter_escola_usuario()

    if not nome:
        flash("Informe o nome do aluno.", "erro")
        return redirect("/alunos")

    if modo_matricula not in ["automatica", "manual"]:
        modo_matricula = "automatica"

    if modo_matricula == "manual" and not matricula:
        flash("Informe o número da matrícula.", "erro")
        return redirect("/alunos")

    if not turma_id:
        flash("Selecione uma turma.", "erro")
        return redirect("/alunos")

    try:
        turma_id = int(turma_id)
    except (TypeError, ValueError):
        flash("A turma selecionada é inválida.", "erro")
        return redirect("/alunos")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        cursor.execute("""
            SELECT
                t.id,
                t.escola_id,
                t.ano_letivo_id,
                al.ano,
                al.ativo,
                al.encerrado
            FROM turmas AS t
            INNER JOIN anos_letivos AS al
                ON al.id = t.ano_letivo_id
               AND al.escola_id = t.escola_id
            WHERE t.id = ?
            LIMIT 1
        """, (turma_id,))

        turma = cursor.fetchone()

        if turma is None:
            flash("A turma selecionada não existe.", "erro")
            return redirect("/alunos")

        escola_id = turma["escola_id"]
        ano_letivo_id = turma["ano_letivo_id"]
        numero_ano = turma["ano"]

        if cargo == "Administrador Geral":
            if not escola_id_form:
                flash("Selecione uma instituição.", "erro")
                return redirect("/alunos")
            try:
                escola_id_selecionada = int(escola_id_form)
            except (TypeError, ValueError):
                flash("A instituição selecionada é inválida.", "erro")
                return redirect("/alunos")
            if escola_id_selecionada != escola_id:
                flash(
                    "A turma selecionada não pertence à instituição informada.",
                    "erro"
                )
                return redirect("/alunos")

        if (
            cargo != "Administrador Geral"
            and escola_id != escola_id_usuario
        ):
            flash(
                "A turma selecionada não pertence à sua instituição.",
                "erro"
            )
            return redirect("/alunos")

        # Matrículas e novos cadastros só podem ser feitos no ano ativo.
        if turma["ativo"] != 1 or turma["encerrado"] == 1:
            flash(
                f"O ano letivo {numero_ano} está em modo de consulta. "
                "Volte ao ano ativo para matricular alunos.",
                "erro"
            )
            return redirect("/alunos")

        # Confirma que a turma pertence ao ano exibido no topo.
        if cargo == "Administrador Geral":
            ano_topo = session.get("ano_letivo_visualizado")
            if ano_topo and int(ano_topo) != int(numero_ano):
                flash(
                    "A turma não pertence ao ano letivo selecionado.",
                    "erro"
                )
                return redirect("/alunos")
        else:
            ano_selecionado = atualizar_ano_letivo_na_sessao(escola_id)
            if (
                not ano_selecionado
                or ano_selecionado["id"] != ano_letivo_id
            ):
                flash(
                    "A turma não pertence ao ano letivo selecionado.",
                    "erro"
                )
                return redirect("/alunos")

        # Gera o número somente depois de identificar corretamente
        # a instituição e o ano letivo da turma selecionada.
        if modo_matricula == "automatica":
            matricula = gerar_numero_matricula(
                cursor,
                escola_id,
                numero_ano
            )

        # A matrícula identifica o cadastro permanente do estudante
        # dentro da instituição. Se já existir, apenas criamos o novo
        # vínculo anual; não duplicamos o aluno.
        cursor.execute("""
            SELECT id, nome
            FROM alunos
            WHERE escola_id = ?
              AND LOWER(TRIM(matricula)) = LOWER(TRIM(?))
            ORDER BY id ASC
            LIMIT 1
        """, (escola_id, matricula))

        aluno_existente = cursor.fetchone()

        if aluno_existente:
            aluno_id = aluno_existente["id"]

            cursor.execute("""
                SELECT id
                FROM aluno_matriculas
                WHERE aluno_id = ?
                  AND ano_letivo_id = ?
                LIMIT 1
            """, (aluno_id, ano_letivo_id))

            if cursor.fetchone():
                flash(
                    "Este aluno já possui matrícula neste ano letivo.",
                    "erro"
                )
                return redirect("/alunos")

            # Mantém o nome atualizado quando o cadastro foi localizado
            # pela matrícula permanente.
            cursor.execute("""
                UPDATE alunos
                SET
                    nome = ?,
                    turma_id = ?,
                    ano_letivo_id = ?
                WHERE id = ?
            """, (
                nome,
                turma_id,
                ano_letivo_id,
                aluno_id
            ))

        else:
            cursor.execute("""
                INSERT INTO alunos (
                    nome,
                    matricula,
                    turma_id,
                    escola_id,
                    ano_letivo_id
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                nome,
                matricula,
                turma_id,
                escola_id,
                ano_letivo_id
            ))

            aluno_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO aluno_matriculas (
                aluno_id,
                escola_id,
                ano_letivo_id,
                turma_id,
                situacao
            )
            VALUES (?, ?, ?, ?, 'Cursando')
        """, (
            aluno_id,
            escola_id,
            ano_letivo_id,
            turma_id
        ))

        banco.commit()

        flash(
            f"Aluno matriculado com sucesso. Número da matrícula: "
            f"{matricula}.",
            "success"
        )

        return redirect("/alunos")

    except sqlite3.IntegrityError as erro:

        banco.rollback()
        print("ERRO DE INTEGRIDADE AO MATRICULAR ALUNO:", erro)

        flash(
            "Não foi possível concluir a matrícula. Verifique se o "
            "aluno já está matriculado neste ano letivo.",
            "erro"
        )

        return redirect("/alunos")

    except sqlite3.Error as erro:

        banco.rollback()

        import traceback
        traceback.print_exc()

        print("ERRO AO MATRICULAR ALUNO:", erro)

        flash(
            f"Erro ao matricular aluno: {erro}",
            "erro"
        )

        return redirect("/alunos")

    finally:
        banco.close()


# =========================================================
# HISTÓRICO DE MATRÍCULAS DO ALUNO
# =========================================================

@app.route("/alunos/<int:aluno_id>")
def historico_aluno(aluno_id):

    if not permissao_modulo("Alunos"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = session.get("usuario_cargo", "").strip()
    escola_id_usuario = obter_escola_usuario()

    try:
        cursor.execute("""
            SELECT
                a.id,
                a.nome,
                a.matricula,
                a.escola_id,
                e.nome_instituicao
            FROM alunos AS a
            LEFT JOIN escolas AS e
                ON e.id = a.escola_id
            WHERE a.id = ?
            LIMIT 1
        """, (aluno_id,))

        aluno = cursor.fetchone()

        if aluno is None:
            flash("Aluno não encontrado.", "erro")
            return redirect("/alunos")

        if (
            cargo != "Administrador Geral"
            and aluno["escola_id"] != escola_id_usuario
        ):
            flash("Você não possui acesso a este aluno.", "erro")
            return redirect("/alunos")

        cursor.execute("""
            SELECT
                am.id,
                am.ano_letivo_id,
                am.turma_id,
                am.situacao,
                am.data_matricula,
                am.data_encerramento,
                am.observacao,
                al.ano AS ano_letivo,
                al.ativo,
                al.encerrado,
                t.nome AS nome_turma,
                t.ano AS ano_turma,
                t.etapa,
                t.turno
            FROM aluno_matriculas AS am
            INNER JOIN anos_letivos AS al
                ON al.id = am.ano_letivo_id
            INNER JOIN turmas AS t
                ON t.id = am.turma_id
            WHERE am.aluno_id = ?
            ORDER BY al.ano DESC
        """, (aluno_id,))

        historico = cursor.fetchall()

        # Compatibilidade para um registro antigo que ainda não tenha
        # sido inserido em aluno_matriculas.
        if not historico:
            cursor.execute("""
                SELECT
                    NULL AS id,
                    a.ano_letivo_id,
                    a.turma_id,
                    'Cursando' AS situacao,
                    NULL AS data_matricula,
                    NULL AS data_encerramento,
                    NULL AS observacao,
                    al.ano AS ano_letivo,
                    al.ativo,
                    al.encerrado,
                    t.nome AS nome_turma,
                    t.ano AS ano_turma,
                    t.etapa,
                    t.turno
                FROM alunos AS a
                INNER JOIN anos_letivos AS al
                    ON al.id = a.ano_letivo_id
                INNER JOIN turmas AS t
                    ON t.id = a.turma_id
                WHERE a.id = ?
                LIMIT 1
            """, (aluno_id,))

            registro_antigo = cursor.fetchone()
            historico = [registro_antigo] if registro_antigo else []

        return render_template(
            "aluno_historico.html",
            aluno=aluno,
            historico=historico
        )

    except sqlite3.Error as erro:
        import traceback
        traceback.print_exc()

        print("ERRO AO CARREGAR HISTÓRICO DO ALUNO:", erro)
        flash(f"Erro ao carregar o histórico do aluno: {erro}", "erro")
        return redirect("/alunos")

    finally:
        banco.close()


@app.route("/professores")
def professores():

    if not permissao_modulo("Professores"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT 
            professores.id,
            professores.nome,
            professores.email,
            COALESCE(
                GROUP_CONCAT(DISTINCT professor_disciplinas.disciplina),
                professores.disciplina,
                ''
            ) AS disciplinas
        FROM professores
        LEFT JOIN professor_disciplinas 
            ON professores.id = professor_disciplinas.professor_id
        GROUP BY professores.id
        ORDER BY professores.nome
    """)
    lista_professores = cursor.fetchall()

    cursor.execute("""
        SELECT *
        FROM turmas
        ORDER BY nome
    """)
    lista_turmas = cursor.fetchall()

    banco.close()

    return render_template(
        "professores.html",
        professores=lista_professores,
        turmas=lista_turmas
    )

@app.route("/cadastrar_professor", methods=["POST"])
def cadastrar_professor():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador"
    ]):
        return redirect("/login")

    nome = request.form["nome"]
    email = request.form["email"]

    disciplinas = request.form.getlist("disciplinas")
    turmas = request.form.getlist("turmas")

    disciplina_principal = ", ".join(disciplinas)

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        INSERT INTO professores (nome, email, disciplina)
        VALUES (?, ?, ?)
    """, (nome, email, disciplina_principal))

    professor_id = cursor.lastrowid

    for disciplina in disciplinas:
        cursor.execute("""
            INSERT INTO professor_disciplinas (professor_id, disciplina)
            VALUES (?, ?)
        """, (professor_id, disciplina))

    for turma_id in turmas:
        cursor.execute("""
            INSERT INTO professor_turmas (professor_id, turma_id)
            VALUES (?, ?)
        """, (professor_id, turma_id))

    banco.commit()
    banco.close()

    return redirect("/professores")

@app.route("/questoes")
def questoes():

    if not permissao_modulo("Questões"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = (session.get("usuario_cargo") or "").strip()
    usuario_id = session.get("usuario_id")
    escola_id = obter_escola_usuario()

    busca = (request.args.get("busca") or "").strip()
    disciplina = (request.args.get("disciplina") or "").strip()
    etapa = (request.args.get("etapa") or "").strip()
    ano_serie = (request.args.get("ano_serie") or "").strip()
    assunto = (request.args.get("assunto") or "").strip()
    tipo = (request.args.get("tipo") or "").strip()
    dificuldade = (request.args.get("dificuldade") or "").strip()
    bloom = (request.args.get("bloom") or "").strip()
    origem = (request.args.get("origem") or "todas").strip()

    pagina = max(request.args.get("pagina", default=1, type=int) or 1, 1)
    por_pagina = 12
    deslocamento = (pagina - 1) * por_pagina

    filtros = []
    parametros = []

    if cargo != "Administrador Geral" and escola_id:
        filtros.append("q.escola_id = ?")
        parametros.append(escola_id)

    if origem == "minhas":
        filtros.append("q.criado_por = ?")
        parametros.append(usuario_id)

    if busca:
        termo = f"%{busca}%"
        filtros.append("(" + " OR ".join([
            "q.enunciado LIKE ?", "q.assunto LIKE ?", "q.subassunto LIKE ?",
            "q.habilidade_bncc LIKE ?", "q.descritor_saeb LIKE ?", "q.tags LIKE ?"
        ]) + ")")
        parametros.extend([termo] * 6)

    campos = [
        (disciplina, "q.disciplina = ?"),
        (etapa, "q.etapa_ensino = ?"),
        (ano_serie, "q.ano_serie = ?"),
        (assunto, "q.assunto = ?"),
        (tipo, "q.tipo_questao = ?"),
        (dificuldade, "q.dificuldade = ?"),
        (bloom, "q.taxonomia_bloom = ?")
    ]
    for valor, sql in campos:
        if valor:
            filtros.append(sql)
            parametros.append(valor)

    where = "WHERE " + " AND ".join(filtros) if filtros else ""

    try:
        cursor.execute(f"SELECT COUNT(*) AS total FROM questoes q {where}", parametros)
        total = cursor.fetchone()["total"]
        total_paginas = max((total + por_pagina - 1) // por_pagina, 1)
        if pagina > total_paginas:
            pagina = total_paginas
            deslocamento = (pagina - 1) * por_pagina

        cursor.execute(f"""
            SELECT
                q.*,
                COALESCE(u.nome, 'ARK EDUS') AS autor_nome,
                COALESCE(e.nome_instituicao, 'Banco compartilhado') AS instituicao_nome,
                (SELECT COUNT(*) FROM prova_questoes pq WHERE pq.questao_id = q.id) AS total_usos
            FROM questoes q
            LEFT JOIN usuarios u ON u.id = q.criado_por
            LEFT JOIN escolas e ON e.id = q.escola_id
            {where}
            ORDER BY COALESCE(q.atualizado_em, q.criado_em) DESC, q.id DESC
            LIMIT ? OFFSET ?
        """, parametros + [por_pagina, deslocamento])
        lista_questoes = cursor.fetchall()

        def distintos(campo):
            condicao = ""
            args = []
            if cargo != "Administrador Geral" and escola_id:
                condicao = "WHERE escola_id = ?"
                args = [escola_id]
            cursor.execute(f"""
                SELECT DISTINCT {campo} AS valor
                FROM questoes
                {condicao}
                {'AND' if condicao else 'WHERE'} {campo} IS NOT NULL
                  AND TRIM({campo}) <> ''
                ORDER BY {campo} COLLATE NOCASE
            """, args)
            return [linha["valor"] for linha in cursor.fetchall()]

        opcoes = {
            "disciplinas": distintos("disciplina"),
            "etapas": distintos("etapa_ensino"),
            "anos": distintos("ano_serie"),
            "assuntos": distintos("assunto"),
            "blooms": distintos("taxonomia_bloom")
        }

        cursor.execute(f"SELECT COUNT(*) AS total FROM questoes q {('WHERE q.escola_id = ?' if cargo != 'Administrador Geral' and escola_id else '')}", ([escola_id] if cargo != 'Administrador Geral' and escola_id else []))
        total_geral = cursor.fetchone()["total"]

        cursor.execute(f"SELECT COUNT(*) AS total FROM questoes q WHERE q.criado_por = ? {('AND q.escola_id = ?' if cargo != 'Administrador Geral' and escola_id else '')}", ([usuario_id, escola_id] if cargo != 'Administrador Geral' and escola_id else [usuario_id]))
        total_minhas = cursor.fetchone()["total"]

        cursor.execute(f"SELECT COUNT(*) AS total FROM questoes q WHERE q.tipo_questao IN ('multipla_escolha','multiplas_respostas','verdadeiro_falso') {('AND q.escola_id = ?' if cargo != 'Administrador Geral' and escola_id else '')}", ([escola_id] if cargo != 'Administrador Geral' and escola_id else []))
        total_objetivas = cursor.fetchone()["total"]

        cursor.execute(f"SELECT COUNT(*) AS total FROM questoes q WHERE q.tipo_questao IN ('discursiva','resposta_curta','numerica') {('AND q.escola_id = ?' if cargo != 'Administrador Geral' and escola_id else '')}", ([escola_id] if cargo != 'Administrador Geral' and escola_id else []))
        total_discursivas = cursor.fetchone()["total"]

        return render_template(
            "questoes/index.html",
            questoes=lista_questoes,
            opcoes=opcoes,
            total=total,
            total_geral=total_geral,
            total_minhas=total_minhas,
            total_objetivas=total_objetivas,
            total_discursivas=total_discursivas,
            pagina=pagina,
            total_paginas=total_paginas,
            filtros_atuais={
                "busca": busca, "disciplina": disciplina, "etapa": etapa,
                "ano_serie": ano_serie, "assunto": assunto, "tipo": tipo,
                "dificuldade": dificuldade, "bloom": bloom, "origem": origem
            }
        )

    except sqlite3.Error as erro:
        print("ERRO AO CARREGAR BANCO DE QUESTÕES:", erro)
        flash("Não foi possível carregar o banco de questões.", "erro")
        return render_template(
            "questoes/index.html", questoes=[], opcoes={}, total=0,
            total_geral=0, total_minhas=0, total_objetivas=0,
            total_discursivas=0, pagina=1, total_paginas=1,
            filtros_atuais={}
        )
    finally:
        banco.close()


@app.route("/questoes/<int:questao_id>")
def visualizar_questao(questao_id):
    if not permissao_modulo("Questões"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cursor.execute("""
            SELECT
                q.*,
                COALESCE(u.nome, 'ARK EDUS') AS autor_nome,
                COALESCE(e.nome_instituicao, 'Banco compartilhado') AS instituicao_nome,
                (
                    SELECT COUNT(*)
                    FROM prova_questoes AS pq
                    WHERE pq.questao_id = q.id
                ) AS total_usos
            FROM questoes AS q
            LEFT JOIN usuarios AS u
                ON u.id = q.criado_por
            LEFT JOIN escolas AS e
                ON e.id = q.escola_id
            WHERE q.id = ?
            LIMIT 1
        """, (questao_id,))

        questao = cursor.fetchone()

        if not questao:
            flash("Questão não encontrada.", "erro")
            return redirect("/questoes")

        cargo = (session.get("usuario_cargo") or "").strip()
        escola_usuario = obter_escola_usuario()

        if (
            cargo != "Administrador Geral"
            and questao["escola_id"] is not None
            and escola_usuario is not None
            and int(questao["escola_id"]) != int(escola_usuario)
        ):
            return redirect("/acesso_negado")

        try:
            alternativas = json.loads(questao["alternativas_json"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            alternativas = []

        if not alternativas:
            alternativas = []
            for indice, campo in enumerate(
                ["alternativa_a", "alternativa_b", "alternativa_c", "alternativa_d"]
            ):
                texto = questao[campo] or ""
                if texto:
                    alternativas.append({
                        "letra": chr(65 + indice),
                        "texto": texto,
                        "imagem": ""
                    })

        try:
            respostas_corretas = json.loads(
                questao["respostas_corretas"] or "[]"
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            respostas_corretas = []

        if not respostas_corretas and questao["correta"]:
            respostas_corretas = [questao["correta"]]

        tags = [
            item.strip()
            for item in (questao["tags"] or "").split(",")
            if item.strip()
        ]

        pode_editar = (
            cargo in [
                "Administrador Geral",
                "Administrador da Instituição",
                "Coordenador"
            ]
            or questao["criado_por"] == session.get("usuario_id")
        )

        return render_template(
            "questoes/visualizar.html",
            questao=questao,
            alternativas=alternativas,
            respostas_corretas=respostas_corretas,
            tags=tags,
            pode_editar=pode_editar
        )

    except sqlite3.Error as erro:
        print("ERRO AO VISUALIZAR QUESTÃO:", erro)
        flash("Não foi possível abrir a questão.", "erro")
        return redirect("/questoes")

    finally:
        banco.close()


@app.route("/questoes/nova")
def nova_questao():
    if not permissao_modulo("Questões"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    componentes = []
    assuntos = []
    valores_anteriores = None
    prova_id = request.args.get("prova_id", type=int)
    escola_filtro_id = obter_escola_usuario()

    try:
        # Quando o cadastro foi aberto durante a montagem de uma avaliação,
        # valida o acesso e utiliza os dados da própria avaliação para
        # preencher automaticamente componente, etapa e ano/série.
        if prova_id:
            if not _pode_gerenciar_prova(
                cursor,
                prova_id,
                exigir_edicao=True
            ):
                return _redirecionar_acesso_negado_prova()

            cursor.execute("""
                SELECT
                    p.id,
                    p.disciplina,
                    COALESCE(p.escola_id, t.escola_id) AS escola_id,
                    t.etapa,
                    t.ano AS turma_ano
                FROM provas AS p
                INNER JOIN turmas AS t
                    ON t.id = p.turma_id
                WHERE p.id = ?
                LIMIT 1
            """, (prova_id,))
            prova_origem = cursor.fetchone()

            if not prova_origem:
                flash("Avaliação não encontrada.", "erro")
                return redirect("/provas")

            escola_filtro_id = prova_origem["escola_id"]
            valores_anteriores = {
                "disciplina": prova_origem["disciplina"] or "",
                "etapa_ensino": prova_origem["etapa"] or "",
                "ano_serie": prova_origem["turma_ano"] or ""
            }

        elif request.args.get("repetir") == "1":
            valores_anteriores = session.get("ultima_classificacao_questao")

        if escola_filtro_id:
            cursor.execute("""
                SELECT DISTINCT nome
                FROM componentes_curriculares
                WHERE ativo = 1
                  AND escola_id = ?
                  AND nome IS NOT NULL
                  AND TRIM(nome) <> ''
                ORDER BY nome COLLATE NOCASE
            """, (escola_filtro_id,))
        else:
            cursor.execute("""
                SELECT DISTINCT nome
                FROM componentes_curriculares
                WHERE ativo = 1
                  AND nome IS NOT NULL
                  AND TRIM(nome) <> ''
                ORDER BY nome COLLATE NOCASE
            """)

        componentes = [linha["nome"] for linha in cursor.fetchall()]

        # Garante que o componente da avaliação apareça mesmo em cadastros
        # antigos que ainda não estejam na tabela de componentes curriculares.
        if valores_anteriores:
            componente_prova = (valores_anteriores.get("disciplina") or "").strip()
            if componente_prova and componente_prova not in componentes:
                componentes.insert(0, componente_prova)

        if escola_filtro_id:
            cursor.execute("""
                SELECT DISTINCT
                    disciplina,
                    etapa_ensino,
                    ano_serie,
                    nome
                FROM assuntos
                WHERE ativo = 1
                  AND (escola_id = ? OR escola_id IS NULL)
                ORDER BY nome COLLATE NOCASE
            """, (escola_filtro_id,))
        else:
            cursor.execute("""
                SELECT DISTINCT
                    disciplina,
                    etapa_ensino,
                    ano_serie,
                    nome
                FROM assuntos
                WHERE ativo = 1
                ORDER BY nome COLLATE NOCASE
            """)

        assuntos = [
            {
                "nome": linha["nome"],
                "disciplina": linha["disciplina"] or "",
                "etapa_ensino": linha["etapa_ensino"] or "",
                "ano_serie": linha["ano_serie"] or ""
            }
            for linha in cursor.fetchall()
        ]

    except sqlite3.Error as erro:
        print("ERRO AO CARREGAR NOVA QUESTÃO:", erro)
        flash("Não foi possível carregar o cadastro da questão.", "erro")

    finally:
        banco.close()

    return render_template(
        "questoes/nova.html",
        componentes=componentes,
        assuntos=assuntos,
        valores_anteriores=valores_anteriores,
        prova_id=prova_id
    )


@app.route("/questoes/<int:questao_id>/excluir", methods=["POST"])
def excluir_questao(questao_id):
    if not permissao_modulo("Questões"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()
    try:
        cursor.execute("SELECT escola_id, criado_por FROM questoes WHERE id = ?", (questao_id,))
        questao = cursor.fetchone()
        if not questao:
            flash("Questão não encontrada.", "erro")
            return redirect("/questoes")

        cargo = (session.get("usuario_cargo") or "").strip()
        escola_id = obter_escola_usuario()
        usuario_id = session.get("usuario_id")
        pode_excluir = cargo in ["Administrador Geral", "Administrador da Instituição", "Coordenador"]
        pode_excluir = pode_excluir or questao["criado_por"] == usuario_id

        if cargo != "Administrador Geral" and questao["escola_id"] not in (None, escola_id):
            pode_excluir = False

        if not pode_excluir:
            return redirect("/acesso_negado")

        cursor.execute("DELETE FROM questoes WHERE id = ?", (questao_id,))
        banco.commit()
        flash("Questão excluída com sucesso.", "sucesso")
    except sqlite3.IntegrityError:
        banco.rollback()
        flash("Esta questão está vinculada a uma avaliação e não pode ser excluída.", "erro")
    except sqlite3.Error as erro:
        banco.rollback()
        flash(f"Não foi possível excluir a questão: {erro}", "erro")
    finally:
        banco.close()

    return redirect(request.referrer or "/questoes")


@app.route("/cadastrar_questao", methods=["POST"])
def cadastrar_questao():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    ]):
        return redirect("/login")

    if not permissao_modulo("Questões"):
        return redirect("/acesso_negado")

    disciplina = (request.form.get("disciplina") or "").strip()
    etapa_ensino = (request.form.get("etapa_ensino") or "").strip()
    ano_serie = (request.form.get("ano_serie") or "").strip()
    assunto = (request.form.get("assunto") or "").strip()
    assunto_outro = (request.form.get("assunto_outro") or "").strip()
    assunto_temporario = request.form.get("assunto_temporario") == "1"
    subassunto = (request.form.get("subassunto") or "").strip()
    tipo_questao = (request.form.get("tipo_questao") or "multipla_escolha").strip()
    enunciado = (request.form.get("enunciado") or "").strip()
    enunciado_html = (request.form.get("enunciado_html") or "").strip()
    dificuldade = (request.form.get("dificuldade") or "").strip()
    taxonomia_bloom = (request.form.get("taxonomia_bloom") or "").strip()
    unidade_tematica = (request.form.get("unidade_tematica") or "").strip()
    objeto_conhecimento = (request.form.get("objeto_conhecimento") or "").strip()
    habilidade_bncc = (request.form.get("habilidade_bncc") or "").strip()
    matriz_referencia = (request.form.get("matriz_referencia") or "").strip()
    descritor_saeb = (request.form.get("descritor_saeb") or "").strip()
    fonte = (request.form.get("fonte") or "").strip()
    tags = (request.form.get("tags") or "").strip()
    observacoes = (request.form.get("observacoes") or "").strip()
    resposta_esperada = (request.form.get("resposta_esperada") or "").strip()
    criterios_correcao = (request.form.get("criterios_correcao") or "").strip()
    manter_classificacao = request.form.get("manter_classificacao") == "1"
    prova_id = request.form.get("prova_id", type=int)
    url_nova_questao = (
        f"/questoes/nova?prova_id={prova_id}"
        if prova_id
        else "/questoes/nova"
    )

    try:
        linhas_resposta = int(request.form.get("linhas_resposta") or 5)
    except (TypeError, ValueError):
        linhas_resposta = 5
    linhas_resposta = max(1, min(linhas_resposta, 30))

    try:
        ano_fonte = int(request.form.get("ano_fonte")) if request.form.get("ano_fonte") else None
    except (TypeError, ValueError):
        ano_fonte = None

    try:
        tempo_estimado = int(request.form.get("tempo_estimado")) if request.form.get("tempo_estimado") else None
    except (TypeError, ValueError):
        tempo_estimado = None

    if assunto == "__outro__":
        assunto = assunto_outro
        assunto_temporario = True

    # Mantém o campo legado para filtros e telas antigas.
    habilidade = " | ".join(
        item for item in [habilidade_bncc, descritor_saeb] if item
    )

    tipos_validos = {
        "multipla_escolha", "verdadeiro_falso", "multiplas_respostas",
        "discursiva", "resposta_curta", "numerica"
    }

    if tipo_questao not in tipos_validos:
        flash("Tipo de questão inválido.", "erro")
        return redirect(url_nova_questao)

    if not disciplina or not assunto or not enunciado or not dificuldade:
        flash("Preencha o componente, o assunto, o enunciado e a dificuldade.", "erro")
        return redirect(url_nova_questao)

    extensoes_permitidas = {".png", ".jpg", ".jpeg", ".webp"}

    def salvar_imagem(arquivo, prefixo):
        if not arquivo or not arquivo.filename:
            return ""
        nome_seguro = secure_filename(arquivo.filename)
        extensao = os.path.splitext(nome_seguro)[1].lower()
        if extensao not in extensoes_permitidas:
            raise ValueError("Envie apenas imagens PNG, JPG, JPEG ou WEBP.")
        nome_arquivo = f"{prefixo}_{uuid.uuid4().hex}{extensao}"
        arquivo.save(os.path.join(app.config["UPLOAD_FOLDER"], nome_arquivo))
        return nome_arquivo

    def salvar_imagens_embutidas(conteudo_html):
        """Converte imagens data URL do editor em arquivos reais no servidor."""
        if not conteudo_html:
            return ""

        padrao = re.compile(
            r'src=["\']data:image/(?P<tipo>png|jpeg|jpg|webp);base64,(?P<dados>[^"\']+)["\']',
            re.IGNORECASE
        )

        def substituir(match):
            tipo = match.group("tipo").lower()
            extensao = ".jpg" if tipo in {"jpg", "jpeg"} else f".{tipo}"
            try:
                dados = base64.b64decode(match.group("dados"), validate=True)
            except Exception as erro:
                raise ValueError("Uma das imagens inseridas no enunciado é inválida.") from erro

            if len(dados) > 8 * 1024 * 1024:
                raise ValueError("Cada imagem do enunciado deve ter no máximo 8 MB.")

            nome_arquivo = f"enunciado_{uuid.uuid4().hex}{extensao}"
            with open(os.path.join(app.config["UPLOAD_FOLDER"], nome_arquivo), "wb") as destino:
                destino.write(dados)
            return f'src="/static/uploads/{nome_arquivo}"'

        conteudo_html = padrao.sub(substituir, conteudo_html)
        conteudo_html = re.sub(r'<\s*(script|iframe|object|embed)[^>]*>.*?<\s*/\s*\1\s*>', '', conteudo_html, flags=re.I | re.S)
        conteudo_html = re.sub(r'\son[a-z]+\s*=\s*(["\']).*?\1', '', conteudo_html, flags=re.I | re.S)
        conteudo_html = re.sub(r'javascript\s*:', '', conteudo_html, flags=re.I)
        return conteudo_html

    try:
        enunciado_html = salvar_imagens_embutidas(enunciado_html)
        nome_imagem = salvar_imagem(request.files.get("imagem"), "questao")
    except ValueError as erro:
        flash(str(erro), "erro")
        return redirect(url_nova_questao)

    alternativas = []
    respostas_corretas = []

    if tipo_questao in {"multipla_escolha", "multiplas_respostas"}:
        textos = request.form.getlist("alternativas[]")
        imagens = request.files.getlist("imagens_alternativas[]")
        indices_corretos = set(request.form.getlist("corretas[]"))
        total_linhas = max(len(textos), len(imagens))

        for indice in range(total_linhas):
            texto = textos[indice].strip() if indice < len(textos) else ""
            arquivo_imagem = imagens[indice] if indice < len(imagens) else None
            try:
                imagem_alternativa = salvar_imagem(arquivo_imagem, "alternativa")
            except ValueError as erro:
                flash(f"Alternativa {indice + 1}: {erro}", "erro")
                return redirect(url_nova_questao)

            if not texto and not imagem_alternativa:
                continue

            letra = chr(65 + len(alternativas))
            alternativas.append({"letra": letra, "texto": texto, "imagem": imagem_alternativa})
            if str(indice) in indices_corretos:
                respostas_corretas.append(letra)

        if len(alternativas) < 2:
            flash("Cadastre pelo menos duas alternativas com texto ou imagem.", "erro")
            return redirect(url_nova_questao)
        if tipo_questao == "multipla_escolha" and len(respostas_corretas) != 1:
            flash("Marque exatamente uma alternativa correta.", "erro")
            return redirect(url_nova_questao)
        if tipo_questao == "multiplas_respostas" and not respostas_corretas:
            flash("Marque pelo menos uma alternativa correta.", "erro")
            return redirect(url_nova_questao)

    elif tipo_questao == "verdadeiro_falso":
        resposta_vf = (request.form.get("resposta_vf") or "").strip()
        if resposta_vf not in {"V", "F"}:
            flash("Selecione Verdadeiro ou Falso.", "erro")
            return redirect(url_nova_questao)
        alternativas = [
            {"letra": "V", "texto": "Verdadeiro", "imagem": ""},
            {"letra": "F", "texto": "Falso", "imagem": ""}
        ]
        respostas_corretas = [resposta_vf]

    elif tipo_questao in {"resposta_curta", "numerica"} and not resposta_esperada:
        flash("Informe a resposta esperada.", "erro")
        return redirect(url_nova_questao)

    textos_legados = []
    for item in alternativas[:4]:
        texto_legado = item.get("texto") or ""
        if not texto_legado and item.get("imagem"):
            texto_legado = "[Alternativa com imagem]"
        textos_legados.append(texto_legado)
    textos_legados += [""] * (4 - len(textos_legados))
    correta_legada = respostas_corretas[0] if respostas_corretas else ""

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        escola_questao_id = obter_escola_usuario()
        prova_destino = None

        if prova_id:
            if not _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=True):
                return _redirecionar_acesso_negado_prova()

            cursor.execute("""
                SELECT
                    p.id,
                    p.disciplina,
                    COALESCE(p.escola_id, t.escola_id) AS escola_id
                FROM provas AS p
                INNER JOIN turmas AS t
                    ON t.id = p.turma_id
                WHERE p.id = ?
                LIMIT 1
            """, (prova_id,))
            prova_destino = cursor.fetchone()

            if not prova_destino:
                flash("A avaliação de destino não foi encontrada.", "erro")
                return redirect("/provas")

            if (prova_destino["disciplina"] or "").strip().lower() != disciplina.lower():
                flash(
                    "O componente da questão precisa ser o mesmo da avaliação.",
                    "erro"
                )
                return redirect(url_nova_questao)

            escola_questao_id = prova_destino["escola_id"]

        cursor.execute("""
            INSERT INTO questoes (
                disciplina, etapa_ensino, ano_serie, assunto,
                assunto_temporario, subassunto,
                tipo_questao, enunciado, enunciado_html, imagem,
                alternativa_a, alternativa_b, alternativa_c, alternativa_d,
                correta, alternativas_json, respostas_corretas,
                resposta_esperada, criterios_correcao, habilidade,
                habilidade_bncc, unidade_tematica, objeto_conhecimento,
                matriz_referencia, descritor_saeb, taxonomia_bloom,
                dificuldade, fonte, ano_fonte, tags, tempo_estimado,
                linhas_resposta, observacoes, escola_id, criado_por, criado_em
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            disciplina, etapa_ensino, ano_serie, assunto,
            1 if assunto_temporario else 0, subassunto,
            tipo_questao, enunciado, enunciado_html, nome_imagem,
            textos_legados[0], textos_legados[1], textos_legados[2], textos_legados[3],
            correta_legada, json.dumps(alternativas, ensure_ascii=False),
            json.dumps(respostas_corretas, ensure_ascii=False), resposta_esperada,
            criterios_correcao, habilidade, habilidade_bncc, unidade_tematica,
            objeto_conhecimento, matriz_referencia, descritor_saeb,
            taxonomia_bloom, dificuldade, fonte, ano_fonte, tags,
            tempo_estimado, linhas_resposta if tipo_questao == "discursiva" else None,
            observacoes, escola_questao_id, session.get("usuario_id"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        questao_id = cursor.lastrowid

        if prova_id:
            cursor.execute("""
                SELECT COALESCE(MAX(ordem), 0) + 1 AS proxima_ordem
                FROM prova_questoes
                WHERE prova_id = ?
            """, (prova_id,))
            proxima_ordem = cursor.fetchone()["proxima_ordem"]

            cursor.execute("""
                INSERT OR IGNORE INTO prova_questoes (
                    prova_id,
                    questao_id,
                    peso,
                    ordem
                )
                VALUES (?, ?, 0, ?)
            """, (prova_id, questao_id, proxima_ordem))

            cursor.execute("""
                UPDATE provas
                SET quantidade = (
                    SELECT COUNT(*) FROM prova_questoes WHERE prova_id = ?
                ), atualizado_em = ?
                WHERE id = ?
            """, (
                prova_id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                prova_id
            ))

        banco.commit()
        flash(
            "Questão cadastrada e adicionada à avaliação!" if prova_id
            else "Questão cadastrada com sucesso!",
            "sucesso"
        )

        if manter_classificacao:
            session["ultima_classificacao_questao"] = {
                "disciplina": disciplina,
                "etapa_ensino": etapa_ensino,
                "ano_serie": ano_serie,
                "assunto": assunto,
                "subassunto": subassunto,
                "dificuldade": dificuldade,
                "taxonomia_bloom": taxonomia_bloom,
                "unidade_tematica": unidade_tematica,
                "objeto_conhecimento": objeto_conhecimento,
                "habilidade_bncc": habilidade_bncc,
                "matriz_referencia": matriz_referencia,
                "descritor_saeb": descritor_saeb,
                "fonte": fonte,
                "ano_fonte": ano_fonte or "",
                "tags": tags,
                "tempo_estimado": tempo_estimado or "",
                "abrir_avancado": bool(
                    unidade_tematica or objeto_conhecimento or habilidade_bncc or
                    matriz_referencia or descritor_saeb or fonte or tags
                )
            }
        else:
            session.pop("ultima_classificacao_questao", None)

    except sqlite3.Error as erro:
        banco.rollback()
        print("ERRO AO CADASTRAR QUESTÃO:", erro)
        flash(f"Não foi possível cadastrar a questão: {erro}", "erro")

    finally:
        banco.close()

    if prova_id:
        return redirect(f"/provas/{prova_id}/montar")

    return redirect("/questoes/nova?repetir=1" if manter_classificacao else "/questoes")


# =========================================================
# PROVAS — LISTAGEM, CADASTRO E MONTAGEM
# =========================================================

def _contexto_provas():
    """Retorna o contexto acadêmico e de acesso usado no módulo Provas."""
    contexto = obter_contexto_plataforma()

    return {
        "usuario_id": contexto.get("usuario_id"),
        "cargo": (contexto.get("cargo") or "").strip(),
        "escola_id": contexto.get("escola_id"),
        "ano_letivo_id": contexto.get("ano_letivo_id"),
        "ano": contexto.get("ano")
    }


def _professor_legado_do_usuario(cursor, usuario_id):
    """
    Compatibiliza o cadastro atual de usuários com a tabela professores,
    que ainda é utilizada pela tabela provas.
    """
    if not usuario_id:
        return None

    cursor.execute("""
        SELECT
            u.id AS usuario_id,
            u.nome,
            u.email,
            u.escola_id
        FROM usuarios AS u
        INNER JOIN cargos AS c
            ON c.id = u.cargo_id
        WHERE u.id = ?
          AND c.nome = 'Professor'
        LIMIT 1
    """, (usuario_id,))

    usuario = cursor.fetchone()

    if not usuario:
        return None

    cursor.execute("""
        SELECT id
        FROM professores
        WHERE escola_id = ?
          AND (
                LOWER(TRIM(COALESCE(email, ''))) =
                    LOWER(TRIM(COALESCE(?, '')))
                OR LOWER(TRIM(nome)) = LOWER(TRIM(?))
          )
        ORDER BY
            CASE
                WHEN LOWER(TRIM(COALESCE(email, ''))) =
                     LOWER(TRIM(COALESCE(?, '')))
                THEN 0
                ELSE 1
            END,
            id
        LIMIT 1
    """, (
        usuario["escola_id"],
        usuario["email"],
        usuario["nome"],
        usuario["email"]
    ))

    professor = cursor.fetchone()

    if professor:
        return professor["id"]

    cursor.execute("""
        INSERT INTO professores (
            nome,
            email,
            disciplina,
            escola_id
        )
        VALUES (?, ?, ?, ?)
    """, (
        usuario["nome"],
        usuario["email"],
        "",
        usuario["escola_id"]
    ))

    return cursor.lastrowid



def _sincronizar_professores_da_escola(cursor, escola_id=None):
    """Sincroniza usuários com cargo Professor com a tabela professores."""
    parametros = []
    filtro_escola = ""

    if escola_id:
        filtro_escola = " AND u.escola_id = ? "
        parametros.append(escola_id)

    cursor.execute(f"""
        SELECT u.id, u.nome, u.email, u.escola_id
        FROM usuarios AS u
        INNER JOIN cargos AS c ON c.id = u.cargo_id
        WHERE c.nome = 'Professor'
          AND u.ativo = 1
          AND u.escola_id IS NOT NULL
          {filtro_escola}
        ORDER BY u.nome COLLATE NOCASE
    """, parametros)

    for usuario in cursor.fetchall():
        cursor.execute("""
            SELECT id
            FROM professores
            WHERE escola_id = ?
              AND (
                    (
                        COALESCE(TRIM(?), '') <> ''
                        AND LOWER(TRIM(COALESCE(email, ''))) = LOWER(TRIM(?))
                    )
                    OR LOWER(TRIM(nome)) = LOWER(TRIM(?))
              )
            ORDER BY id
            LIMIT 1
        """, (
            usuario["escola_id"],
            usuario["email"],
            usuario["email"],
            usuario["nome"]
        ))

        existente = cursor.fetchone()

        if existente:
            cursor.execute("""
                UPDATE professores
                SET nome = ?, email = ?, escola_id = ?
                WHERE id = ?
            """, (
                usuario["nome"],
                usuario["email"],
                usuario["escola_id"],
                existente["id"]
            ))
        else:
            cursor.execute("""
                INSERT INTO professores (nome, email, disciplina, escola_id)
                VALUES (?, ?, '', ?)
            """, (
                usuario["nome"],
                usuario["email"],
                usuario["escola_id"]
            ))


def _pode_criar_prova(cargo):
    return cargo in {
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    }


def _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=False):
    """
    Valida o acesso no backend. Assim, alterar manualmente a URL não
    permite acessar uma avaliação de outra instituição ou professor.
    """
    contexto = _contexto_provas()
    cargo = contexto["cargo"]

    cursor.execute("""
        SELECT
            p.id,
            p.professor_id,
            p.escola_id,
            p.ano_letivo_id,
            p.status,
            t.escola_id AS turma_escola_id,
            t.ano_letivo_id AS turma_ano_letivo_id
        FROM provas AS p
        INNER JOIN turmas AS t
            ON t.id = p.turma_id
        WHERE p.id = ?
        LIMIT 1
    """, (prova_id,))

    prova = cursor.fetchone()

    if not prova:
        return False

    # Avaliação finalizada é somente leitura para qualquer perfil.
    if exigir_edicao and (prova["status"] or "rascunho").strip().lower() == "finalizada":
        return False

    if cargo == "Administrador Geral":
        return True

    escola_prova = prova["escola_id"] or prova["turma_escola_id"]

    if (
        not contexto["escola_id"]
        or int(escola_prova or 0) != int(contexto["escola_id"])
    ):
        return False

    ano_prova = prova["ano_letivo_id"] or prova["turma_ano_letivo_id"]

    if contexto["ano_letivo_id"] and ano_prova:
        if int(ano_prova) != int(contexto["ano_letivo_id"]):
            return False

    if cargo == "Professor":
        professor_id = _professor_legado_do_usuario(
            cursor,
            contexto["usuario_id"]
        )
        return bool(
            professor_id
            and int(prova["professor_id"] or 0) == int(professor_id)
        )

    if exigir_edicao:
        return cargo in {
            "Administrador da Instituição",
            "Coordenador"
        }

    return cargo in {
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    }


def _redirecionar_acesso_negado_prova():
    flash(
        "Você não possui permissão para acessar esta avaliação.",
        "erro"
    )
    return redirect("/provas")


@app.route("/provas")
def provas():
    if not permissao_modulo("Provas"):
        return redirect("/acesso_negado")

    contexto = _contexto_provas()
    cargo = contexto["cargo"]
    usuario_id = contexto["usuario_id"]
    escola_id = contexto["escola_id"]
    ano_letivo_id = contexto["ano_letivo_id"]
    ano_visualizado = contexto["ano"]

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    escolas = []
    turmas = []
    professores = []
    registros = []
    professor_logado_id = None

    try:
        if cargo == "Administrador Geral":
            _sincronizar_professores_da_escola(cursor)
            banco.commit()

            cursor.execute("""
                SELECT id, nome_instituicao
                FROM escolas
                WHERE COALESCE(status, 1) = 1
                ORDER BY nome_instituicao COLLATE NOCASE
            """)
            escolas = cursor.fetchall()

            if ano_visualizado is not None:
                cursor.execute("""
                    SELECT DISTINCT
                        t.id,
                        t.nome,
                        t.etapa,
                        t.ano,
                        t.turno,
                        t.escola_id,
                        e.nome_instituicao
                    FROM turmas AS t
                    INNER JOIN escolas AS e
                        ON e.id = t.escola_id
                    INNER JOIN anos_letivos AS al
                        ON al.id = t.ano_letivo_id
                       AND al.escola_id = t.escola_id
                    WHERE al.ano = ?
                    ORDER BY
                        e.nome_instituicao COLLATE NOCASE,
                        t.nome COLLATE NOCASE
                """, (ano_visualizado,))
                turmas = cursor.fetchall()

                cursor.execute("""
                    SELECT
                        pr.id,
                        pr.nome,
                        pr.escola_id,
                        e.nome_instituicao
                    FROM professores AS pr
                    INNER JOIN escolas AS e
                        ON e.id = pr.escola_id
                    WHERE COALESCE(e.status, 1) = 1
                    ORDER BY
                        e.nome_instituicao COLLATE NOCASE,
                        pr.nome COLLATE NOCASE
                """)
                professores = cursor.fetchall()

                cursor.execute("""
                    SELECT
                        p.id,
                        p.nome,
                        p.disciplina,
                        p.data_geracao,
                        p.data_aplicacao,
                        p.status,
                        p.escola_id,
                        t.nome AS turma_nome,
                        e.nome_instituicao,
                        COALESCE(pr.nome, 'Não informado') AS professor_nome,
                        COUNT(pq.id) AS quantidade_real
                    FROM provas AS p
                    INNER JOIN turmas AS t
                        ON t.id = p.turma_id
                    INNER JOIN escolas AS e
                        ON e.id = COALESCE(p.escola_id, t.escola_id)
                    INNER JOIN anos_letivos AS al
                        ON al.id = COALESCE(
                            p.ano_letivo_id,
                            t.ano_letivo_id
                        )
                    LEFT JOIN professores AS pr
                        ON pr.id = p.professor_id
                    LEFT JOIN prova_questoes AS pq
                        ON pq.prova_id = p.id
                    WHERE al.ano = ?
                    GROUP BY p.id
                    ORDER BY p.id DESC
                """, (ano_visualizado,))
                registros = cursor.fetchall()

        else:
            if not escola_id:
                flash(
                    "Não foi possível identificar sua instituição.",
                    "erro"
                )
            else:
                _sincronizar_professores_da_escola(cursor, escola_id)
                banco.commit()

                if not ano_letivo_id:
                    ano = atualizar_ano_letivo_na_sessao(escola_id)
                    ano_letivo_id = ano["id"] if ano else None

                if cargo == "Professor":
                    professor_logado_id = _professor_legado_do_usuario(
                        cursor,
                        usuario_id
                    )

                    cursor.execute("""
                        SELECT DISTINCT
                            t.id,
                            t.nome,
                            t.etapa,
                            t.ano,
                            t.turno,
                            t.escola_id
                        FROM turmas AS t
                        INNER JOIN professor_vinculos AS pv
                            ON pv.turma_id = t.id
                        WHERE pv.professor_id = ?
                          AND t.escola_id = ?
                          AND t.ano_letivo_id = ?
                        ORDER BY t.nome COLLATE NOCASE
                    """, (
                        usuario_id,
                        escola_id,
                        ano_letivo_id
                    ))
                    turmas = cursor.fetchall()

                    if professor_logado_id:
                        cursor.execute("""
                            SELECT id, nome, escola_id
                            FROM professores
                            WHERE id = ?
                        """, (professor_logado_id,))
                        professores = cursor.fetchall()

                        cursor.execute("""
                            SELECT
                                p.id,
                                p.nome,
                                p.disciplina,
                                p.data_geracao,
                                p.data_aplicacao,
                                p.status,
                                p.escola_id,
                                t.nome AS turma_nome,
                                e.nome_instituicao,
                                COALESCE(
                                    pr.nome,
                                    'Não informado'
                                ) AS professor_nome,
                                COUNT(pq.id) AS quantidade_real
                            FROM provas AS p
                            INNER JOIN turmas AS t
                                ON t.id = p.turma_id
                            INNER JOIN escolas AS e
                                ON e.id = t.escola_id
                            LEFT JOIN professores AS pr
                                ON pr.id = p.professor_id
                            LEFT JOIN prova_questoes AS pq
                                ON pq.prova_id = p.id
                            WHERE p.professor_id = ?
                              AND t.escola_id = ?
                              AND COALESCE(
                                  p.ano_letivo_id,
                                  t.ano_letivo_id
                              ) = ?
                            GROUP BY p.id
                            ORDER BY p.id DESC
                        """, (
                            professor_logado_id,
                            escola_id,
                            ano_letivo_id
                        ))
                        registros = cursor.fetchall()

                else:
                    cursor.execute("""
                        SELECT
                            id,
                            nome,
                            etapa,
                            ano,
                            turno,
                            escola_id
                        FROM turmas
                        WHERE escola_id = ?
                          AND ano_letivo_id = ?
                        ORDER BY nome COLLATE NOCASE
                    """, (escola_id, ano_letivo_id))
                    turmas = cursor.fetchall()

                    cursor.execute("""
                        SELECT id, nome, escola_id
                        FROM professores
                        WHERE escola_id = ?
                        ORDER BY nome COLLATE NOCASE
                    """, (escola_id,))
                    professores = cursor.fetchall()

                    cursor.execute("""
                        SELECT
                            p.id,
                            p.nome,
                            p.disciplina,
                            p.data_geracao,
                            p.data_aplicacao,
                            p.status,
                            p.escola_id,
                            t.nome AS turma_nome,
                            e.nome_instituicao,
                            COALESCE(
                                pr.nome,
                                'Não informado'
                            ) AS professor_nome,
                            COUNT(pq.id) AS quantidade_real
                        FROM provas AS p
                        INNER JOIN turmas AS t
                            ON t.id = p.turma_id
                        INNER JOIN escolas AS e
                            ON e.id = t.escola_id
                        LEFT JOIN professores AS pr
                            ON pr.id = p.professor_id
                        LEFT JOIN prova_questoes AS pq
                            ON pq.prova_id = p.id
                        WHERE t.escola_id = ?
                          AND COALESCE(
                              p.ano_letivo_id,
                              t.ano_letivo_id
                          ) = ?
                        GROUP BY p.id
                        ORDER BY p.id DESC
                    """, (escola_id, ano_letivo_id))
                    registros = cursor.fetchall()

        hoje = datetime.now().date()
        lista_provas = []

        for registro in registros:
            data_aplicacao = (
                registro["data_aplicacao"] or ""
            ).strip()
            data_objeto = None

            for formato in ("%Y-%m-%d", "%d/%m/%Y"):
                if not data_aplicacao:
                    break
                try:
                    data_objeto = datetime.strptime(
                        data_aplicacao,
                        formato
                    ).date()
                    break
                except ValueError:
                    continue

            status_banco = (
                registro["status"] or "rascunho"
            ).strip().lower()

            if status_banco == "finalizada":
                if data_objeto and data_objeto > hoje:
                    status = "Agendada"
                    status_slug = "agendada"
                elif data_aplicacao:
                    status = "Aplicada"
                    status_slug = "aplicada"
                else:
                    status = "Finalizada"
                    status_slug = "aplicada"
            else:
                status = "Rascunho"
                status_slug = "rascunho"

            lista_provas.append({
                "id": registro["id"],
                "nome": registro["nome"],
                "turma": registro["turma_nome"],
                "professor": registro["professor_nome"],
                "disciplina": registro["disciplina"],
                "quantidade": registro["quantidade_real"] or 0,
                "data_geracao": (
                    registro["data_geracao"]
                    or "Não informada"
                ),
                "data_aplicacao": (
                    data_objeto.strftime("%d/%m/%Y")
                    if data_objeto
                    else data_aplicacao or "Não definida"
                ),
                "status": status,
                "status_slug": status_slug,
                "escola_id": registro["escola_id"],
                "instituicao": registro["nome_instituicao"]
            })

        indicadores = {
            "total": len(lista_provas),
            "aplicadas": sum(
                item["status_slug"] == "aplicada"
                for item in lista_provas
            ),
            "agendadas": sum(
                item["status_slug"] == "agendada"
                for item in lista_provas
            ),
            "rascunhos": sum(
                item["status_slug"] == "rascunho"
                for item in lista_provas
            )
        }

        disciplinas = sorted({
            item["disciplina"]
            for item in lista_provas
            if item["disciplina"]
        })

        pode_criar = _pode_criar_prova(cargo)
        pode_editar = _pode_criar_prova(cargo)
        pode_excluir = _pode_criar_prova(cargo)

        return render_template(
            "provas.html",
            provas=lista_provas,
            turmas=turmas,
            professores=professores,
            escolas=escolas,
            disciplinas=disciplinas,
            indicadores=indicadores,
            pode_criar=pode_criar,
            pode_editar=pode_editar,
            pode_excluir=pode_excluir,
            exibir_instituicao=(
                cargo == "Administrador Geral"
            ),
            professor_logado_id=professor_logado_id,
            cargo=cargo
        )

    except sqlite3.Error as erro:
        import traceback
        traceback.print_exc()

        flash(
            f"Não foi possível carregar as avaliações: {erro}",
            "erro"
        )

        return render_template(
            "provas.html",
            provas=[],
            turmas=[],
            professores=[],
            escolas=[],
            disciplinas=[],
            indicadores={
                "total": 0,
                "aplicadas": 0,
                "agendadas": 0,
                "rascunhos": 0
            },
            pode_criar=False,
            pode_editar=False,
            pode_excluir=False,
            exibir_instituicao=False,
            cargo=cargo
        )

    finally:
        banco.close()


@app.route("/gerar_prova", methods=["POST"])
def gerar_prova():
    """
    Cria apenas os dados básicos da avaliação.

    As questões são escolhidas depois, na página de montagem.
    """
    if not permissao_modulo("Provas"):
        return redirect("/acesso_negado")

    contexto = _contexto_provas()
    cargo = contexto["cargo"]
    usuario_id = contexto["usuario_id"]

    if not _pode_criar_prova(cargo):
        return _redirecionar_acesso_negado_prova()

    nome = request.form.get("nome", "").strip()
    disciplina = request.form.get("disciplina", "").strip()
    data_aplicacao = request.form.get(
        "data_aplicacao",
        ""
    ).strip()

    media_ativa = 1 if request.form.get("media_ativa") == "1" else 0
    media_aprovacao = None

    if media_ativa:
        media_texto = request.form.get("media_aprovacao", "").strip().replace(",", ".")
        try:
            media_aprovacao = float(media_texto)
        except (TypeError, ValueError):
            flash("Informe uma média válida entre 0,0 e 10,0.", "erro")
            return redirect("/provas")

        if not 0 <= media_aprovacao <= 10:
            flash("A média deve estar entre 0,0 e 10,0.", "erro")
            return redirect("/provas")

    try:
        turma_id = int(
            request.form.get("turma_id", "").strip()
        )
    except (TypeError, ValueError):
        flash("Selecione uma turma válida.", "erro")
        return redirect("/provas")

    if not nome:
        flash("Informe o nome da avaliação.", "erro")
        return redirect("/provas")

    if not disciplina:
        flash(
            "Informe o componente curricular da avaliação.",
            "erro"
        )
        return redirect("/provas")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cursor.execute("""
            SELECT
                t.id,
                t.escola_id,
                t.ano_letivo_id
            FROM turmas AS t
            WHERE t.id = ?
            LIMIT 1
        """, (turma_id,))
        turma = cursor.fetchone()

        if not turma:
            flash("A turma selecionada não foi encontrada.", "erro")
            return redirect("/provas")

        if cargo == "Administrador Geral":
            try:
                escola_id = int(
                    request.form.get("escola_id", "").strip()
                )
            except (TypeError, ValueError):
                flash("Selecione uma instituição.", "erro")
                return redirect("/provas")

            if int(turma["escola_id"]) != escola_id:
                flash(
                    "A turma não pertence à instituição selecionada.",
                    "erro"
                )
                return redirect("/provas")
        else:
            escola_id = contexto["escola_id"]

            if (
                not escola_id
                or int(turma["escola_id"]) != int(escola_id)
            ):
                return _redirecionar_acesso_negado_prova()

        ano_letivo_id = turma["ano_letivo_id"]

        if cargo == "Professor":
            cursor.execute("""
                SELECT 1
                FROM professor_vinculos
                WHERE professor_id = ?
                  AND turma_id = ?
                LIMIT 1
            """, (usuario_id, turma_id))

            if not cursor.fetchone():
                flash(
                    "Você não possui vínculo com essa turma.",
                    "erro"
                )
                return redirect("/provas")

            professor_id = _professor_legado_do_usuario(
                cursor,
                usuario_id
            )
        else:
            try:
                professor_id = int(
                    request.form.get(
                        "professor_id",
                        ""
                    ).strip()
                )
            except (TypeError, ValueError):
                flash("Selecione um professor.", "erro")
                return redirect("/provas")

            cursor.execute("""
                SELECT id
                FROM professores
                WHERE id = ?
                  AND escola_id = ?
                LIMIT 1
            """, (professor_id, escola_id))

            if not cursor.fetchone():
                flash(
                    "O professor selecionado não pertence à instituição.",
                    "erro"
                )
                return redirect("/provas")

        data_geracao = datetime.now().strftime("%d/%m/%Y")
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT INTO provas (
                nome,
                turma_id,
                professor_id,
                disciplina,
                quantidade,
                data_geracao,
                data_aplicacao,
                escola_id,
                ano_letivo_id,
                status,
                atualizado_em,
                media_ativa,
                media_aprovacao
            )
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 'rascunho', ?, ?, ?)
        """, (
            nome,
            turma_id,
            professor_id,
            disciplina,
            data_geracao,
            data_aplicacao,
            escola_id,
            ano_letivo_id,
            agora,
            media_ativa,
            media_aprovacao
        ))

        prova_id = cursor.lastrowid
        banco.commit()

        flash(
            "Dados da avaliação salvos. Agora adicione as questões.",
            "sucesso"
        )

        return redirect(f"/provas/{prova_id}/montar")

    except sqlite3.Error as erro:
        banco.rollback()
        import traceback
        traceback.print_exc()

        flash(
            f"Não foi possível criar a avaliação: {erro}",
            "erro"
        )
        return redirect("/provas")

    finally:
        banco.close()




@app.route("/questoes/<int:questao_id>/editar", methods=["GET", "POST"])
def editar_questao(questao_id):
    if not permissao_modulo("Questões"):
        return redirect("/acesso_negado")

    prova_id = request.args.get("prova_id", type=int)
    if request.method == "POST":
        prova_id = request.form.get("prova_id", type=int)

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cursor.execute("SELECT * FROM questoes WHERE id = ? LIMIT 1", (questao_id,))
        questao = cursor.fetchone()

        if not questao:
            flash("Questão não encontrada.", "erro")
            return redirect(f"/provas/{prova_id}/montar" if prova_id else "/questoes")

        escola_usuario = obter_escola_usuario()
        cargo = (session.get("usuario_cargo") or "").strip()

        if (
            cargo != "Administrador Geral"
            and questao["escola_id"] is not None
            and escola_usuario is not None
            and int(questao["escola_id"]) != int(escola_usuario)
        ):
            return redirect("/acesso_negado")

        if prova_id and not _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=True):
            return _redirecionar_acesso_negado_prova()

        if request.method == "GET":
            try:
                alternativas = json.loads(questao["alternativas_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                alternativas = []

            if not alternativas:
                alternativas = []
                for indice, campo in enumerate(
                    ["alternativa_a", "alternativa_b", "alternativa_c", "alternativa_d"]
                ):
                    texto = questao[campo] or ""
                    if texto:
                        alternativas.append({
                            "letra": chr(65 + indice),
                            "texto": texto,
                            "imagem": ""
                        })

            try:
                respostas = json.loads(questao["respostas_corretas"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                respostas = []

            if not respostas and questao["correta"]:
                respostas = [questao["correta"]]

            cursor.execute("""
                SELECT DISTINCT nome
                FROM componentes_curriculares
                WHERE ativo = 1
                  AND (escola_id = ? OR ? IS NULL)
                ORDER BY nome
            """, (escola_usuario, escola_usuario))
            componentes = [linha["nome"] for linha in cursor.fetchall()]

            return render_template(
                "editar_questao.html",
                questao=questao,
                alternativas=alternativas,
                respostas=respostas,
                componentes=componentes,
                prova_id=prova_id
            )

        disciplina = (request.form.get("disciplina") or "").strip()
        assunto = (request.form.get("assunto") or "").strip()
        dificuldade = (request.form.get("dificuldade") or "").strip()
        tipo_questao = (request.form.get("tipo_questao") or "multipla_escolha").strip()
        enunciado = (request.form.get("enunciado") or "").strip()
        resposta_esperada = (request.form.get("resposta_esperada") or "").strip()
        criterios_correcao = (request.form.get("criterios_correcao") or "").strip()

        try:
            linhas_resposta = int(request.form.get("linhas_resposta") or 5)
        except (TypeError, ValueError):
            linhas_resposta = 5
        linhas_resposta = max(1, min(linhas_resposta, 30))

        if not disciplina or not assunto or not dificuldade or not enunciado:
            flash("Preencha componente, assunto, dificuldade e enunciado.", "erro")
            return redirect(
                f"/questoes/{questao_id}/editar"
                + (f"?prova_id={prova_id}" if prova_id else "")
            )

        tipos_validos = {
            "multipla_escolha", "multiplas_respostas", "verdadeiro_falso",
            "discursiva", "resposta_curta", "numerica"
        }
        if tipo_questao not in tipos_validos:
            flash("Tipo de questão inválido.", "erro")
            return redirect(
                f"/questoes/{questao_id}/editar"
                + (f"?prova_id={prova_id}" if prova_id else "")
            )

        alternativas = []
        respostas_corretas = []

        if tipo_questao in {"multipla_escolha", "multiplas_respostas"}:
            textos = request.form.getlist("alternativas[]")
            corretas = set(request.form.getlist("corretas[]"))

            for indice, texto in enumerate(textos):
                texto = (texto or "").strip()
                if not texto:
                    continue
                letra = chr(65 + len(alternativas))
                alternativas.append({"letra": letra, "texto": texto, "imagem": ""})
                if str(indice) in corretas:
                    respostas_corretas.append(letra)

            if len(alternativas) < 2:
                flash("Cadastre pelo menos duas alternativas.", "erro")
                return redirect(
                    f"/questoes/{questao_id}/editar"
                    + (f"?prova_id={prova_id}" if prova_id else "")
                )
            if tipo_questao == "multipla_escolha" and len(respostas_corretas) != 1:
                flash("Marque exatamente uma alternativa correta.", "erro")
                return redirect(
                    f"/questoes/{questao_id}/editar"
                    + (f"?prova_id={prova_id}" if prova_id else "")
                )
            if tipo_questao == "multiplas_respostas" and not respostas_corretas:
                flash("Marque pelo menos uma alternativa correta.", "erro")
                return redirect(
                    f"/questoes/{questao_id}/editar"
                    + (f"?prova_id={prova_id}" if prova_id else "")
                )

        elif tipo_questao == "verdadeiro_falso":
            resposta_vf = (request.form.get("resposta_vf") or "").strip()
            if resposta_vf not in {"V", "F"}:
                flash("Selecione Verdadeiro ou Falso.", "erro")
                return redirect(
                    f"/questoes/{questao_id}/editar"
                    + (f"?prova_id={prova_id}" if prova_id else "")
                )
            alternativas = [
                {"letra": "V", "texto": "Verdadeiro", "imagem": ""},
                {"letra": "F", "texto": "Falso", "imagem": ""}
            ]
            respostas_corretas = [resposta_vf]

        elif tipo_questao in {"resposta_curta", "numerica"} and not resposta_esperada:
            flash("Informe a resposta esperada.", "erro")
            return redirect(
                f"/questoes/{questao_id}/editar"
                + (f"?prova_id={prova_id}" if prova_id else "")
            )

        textos_legados = [(item.get("texto") or "") for item in alternativas[:4]]
        textos_legados += [""] * (4 - len(textos_legados))
        correta_legada = respostas_corretas[0] if respostas_corretas else ""

        cursor.execute("""
            UPDATE questoes
            SET disciplina = ?, assunto = ?, dificuldade = ?,
                tipo_questao = ?, enunciado = ?,
                alternativa_a = ?, alternativa_b = ?,
                alternativa_c = ?, alternativa_d = ?,
                correta = ?, alternativas_json = ?,
                respostas_corretas = ?, resposta_esperada = ?,
                criterios_correcao = ?, linhas_resposta = ?,
                atualizado_em = ?
            WHERE id = ?
        """, (
            disciplina, assunto, dificuldade, tipo_questao, enunciado,
            textos_legados[0], textos_legados[1],
            textos_legados[2], textos_legados[3],
            correta_legada,
            json.dumps(alternativas, ensure_ascii=False),
            json.dumps(respostas_corretas, ensure_ascii=False),
            resposta_esperada, criterios_correcao,
            linhas_resposta if tipo_questao == "discursiva" else None,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            questao_id
        ))

        banco.commit()
        flash("Questão atualizada com sucesso.", "sucesso")
        return redirect(f"/provas/{prova_id}/montar" if prova_id else "/questoes")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(f"Não foi possível editar a questão: {erro}", "erro")
        return redirect(f"/provas/{prova_id}/montar" if prova_id else "/questoes")
    finally:
        banco.close()


def normalizar_ordem_questoes_prova(cursor, prova_id):
    """Garante uma sequência de ordem 1, 2, 3... para a avaliação."""
    cursor.execute("""
        SELECT id
        FROM prova_questoes
        WHERE prova_id = ?
        ORDER BY
            CASE WHEN COALESCE(ordem, 0) <= 0 THEN 1 ELSE 0 END,
            ordem,
            id
    """, (prova_id,))

    for indice, registro in enumerate(cursor.fetchall(), start=1):
        cursor.execute("""
            UPDATE prova_questoes
            SET ordem = ?
            WHERE id = ? AND prova_id = ?
        """, (indice, registro["id"], prova_id))


@app.route("/provas/<int:prova_id>/montar")
def montar_prova(prova_id):
    if not permissao_modulo("Provas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=False
        ):
            return _redirecionar_acesso_negado_prova()

        cursor.execute("""
            SELECT
                p.id,
                p.nome,
                p.disciplina,
                p.data_aplicacao,
                p.status,
                COALESCE(p.peso_total, 10) AS peso_total,
                COALESCE(p.tipo_peso, 'automatico') AS tipo_peso,
                p.escola_id,
                p.ano_letivo_id,
                t.id AS turma_id,
                t.nome AS turma_nome,
                t.etapa,
                t.ano AS turma_ano,
                t.turno,
                e.nome_instituicao,
                COALESCE(
                    pr.nome,
                    'Não informado'
                ) AS professor_nome
            FROM provas AS p
            INNER JOIN turmas AS t
                ON t.id = p.turma_id
            INNER JOIN escolas AS e
                ON e.id = COALESCE(
                    p.escola_id,
                    t.escola_id
                )
            LEFT JOIN professores AS pr
                ON pr.id = p.professor_id
            WHERE p.id = ?
            LIMIT 1
        """, (prova_id,))
        prova = cursor.fetchone()

        if not prova:
            flash("Avaliação não encontrada.", "erro")
            return redirect("/provas")

        if (prova["status"] or "rascunho").strip().lower() == "finalizada":
            flash("Esta avaliação está finalizada e disponível somente para visualização.", "aviso")
            return redirect(f"/prova/{prova_id}")

        normalizar_ordem_questoes_prova(cursor, prova_id)
        banco.commit()

        cursor.execute("""
            SELECT
                pq.id AS vinculo_id,
                COALESCE(pq.peso, 0) AS peso,
                COALESCE(pq.ordem, 0) AS ordem,
                q.*
            FROM prova_questoes AS pq
            INNER JOIN questoes AS q
                ON q.id = pq.questao_id
            WHERE pq.prova_id = ?
            ORDER BY pq.ordem, pq.id
        """, (prova_id,))
        questoes_adicionadas = cursor.fetchall()

        cursor.execute("""
            SELECT q.*
            FROM questoes AS q
            WHERE q.disciplina = ?
              AND (
                    q.escola_id = ?
                    OR q.escola_id IS NULL
              )
              AND q.id NOT IN (
                    SELECT questao_id
                    FROM prova_questoes
                    WHERE prova_id = ?
              )
            ORDER BY q.id DESC
        """, (
            prova["disciplina"],
            prova["escola_id"],
            prova_id
        ))
        banco_questoes = cursor.fetchall()

        return render_template(
            "montar_prova.html",
            prova=prova,
            questoes=questoes_adicionadas,
            banco_questoes=banco_questoes,
            total_questoes=len(questoes_adicionadas)
        )

    except sqlite3.Error as erro:
        import traceback
        traceback.print_exc()

        flash(
            f"Não foi possível abrir a montagem: {erro}",
            "erro"
        )
        return redirect("/provas")

    finally:
        banco.close()


@app.route("/provas/<int:prova_id>/banco-questoes")
def selecionar_questoes_prova(prova_id):
    if not permissao_modulo("Provas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=True):
            flash("Esta avaliação não pode mais ser editada.", "aviso")
            return redirect(f"/provas/{prova_id}/montar")

        cursor.execute("""
            SELECT p.id, p.nome, p.disciplina, p.escola_id, p.status,
                   t.escola_id AS turma_escola_id, t.nome AS turma_nome
            FROM provas p
            INNER JOIN turmas t ON t.id = p.turma_id
            WHERE p.id = ?
            LIMIT 1
        """, (prova_id,))
        prova = cursor.fetchone()

        if not prova:
            flash("Avaliação não encontrada.", "erro")
            return redirect("/provas")

        busca = request.args.get("busca", "").strip()
        assunto = request.args.get("assunto", "").strip()
        dificuldade = request.args.get("dificuldade", "").strip()
        tipo = request.args.get("tipo", "").strip()
        etapa = request.args.get("etapa", "").strip()
        ano_serie = request.args.get("ano_serie", "").strip()

        escola_id = prova["escola_id"] or prova["turma_escola_id"]
        filtros = [prova["disciplina"], escola_id, prova_id]
        condicoes = [
            "q.disciplina = ?",
            "(q.escola_id = ? OR q.escola_id IS NULL)",
            "q.id NOT IN (SELECT questao_id FROM prova_questoes WHERE prova_id = ?)"
        ]

        if busca:
            condicoes.append("(q.enunciado LIKE ? OR COALESCE(q.assunto, '') LIKE ?)")
            termo = f"%{busca}%"
            filtros.extend([termo, termo])
        if assunto:
            condicoes.append("q.assunto = ?")
            filtros.append(assunto)
        if dificuldade:
            condicoes.append("q.dificuldade = ?")
            filtros.append(dificuldade)
        if tipo:
            condicoes.append("q.tipo_questao = ?")
            filtros.append(tipo)
        if etapa:
            condicoes.append("q.etapa_ensino = ?")
            filtros.append(etapa)
        if ano_serie:
            condicoes.append("q.ano_serie = ?")
            filtros.append(ano_serie)

        cursor.execute(f"""
            SELECT q.*
            FROM questoes q
            WHERE {' AND '.join(condicoes)}
            ORDER BY q.id DESC
        """, filtros)

        questoes = []
        for registro in cursor.fetchall():
            questao = dict(registro)

            try:
                alternativas = json.loads(questao.get("alternativas_json") or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                alternativas = []

            if not alternativas:
                alternativas = []
                for indice, campo in enumerate(
                    ["alternativa_a", "alternativa_b", "alternativa_c", "alternativa_d"]
                ):
                    texto = (questao.get(campo) or "").strip()
                    if texto:
                        alternativas.append({
                            "letra": chr(65 + indice),
                            "texto": texto,
                            "imagem": ""
                        })

            questao["alternativas"] = alternativas
            questoes.append(questao)

        cursor.execute("""
            SELECT DISTINCT assunto FROM questoes
            WHERE disciplina = ? AND TRIM(COALESCE(assunto, '')) <> ''
            ORDER BY assunto
        """, (prova["disciplina"],))
        assuntos = [r["assunto"] for r in cursor.fetchall()]

        return render_template(
            "selecionar_questoes_prova.html",
            prova=prova, questoes=questoes, assuntos=assuntos,
            filtros={"busca": busca, "assunto": assunto, "dificuldade": dificuldade,
                     "tipo": tipo, "etapa": etapa, "ano_serie": ano_serie}
        )
    except sqlite3.Error as erro:
        flash(f"Não foi possível abrir o banco de questões: {erro}", "erro")
        return redirect(f"/provas/{prova_id}/montar")
    finally:
        banco.close()


@app.route("/provas/<int:prova_id>/banco-questoes/adicionar", methods=["POST"])
def adicionar_questoes_selecionadas(prova_id):
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=True):
            flash("Esta avaliação não pode mais ser editada.", "aviso")
            return redirect(f"/provas/{prova_id}/montar")

        ids_brutos = request.form.getlist("questoes_ids")
        questoes_ids = []
        for valor in ids_brutos:
            try:
                questoes_ids.append(int(valor))
            except (TypeError, ValueError):
                continue

        if not questoes_ids:
            flash("Selecione pelo menos uma questão.", "aviso")
            return redirect(f"/provas/{prova_id}/banco-questoes")

        cursor.execute("""
            SELECT p.disciplina, p.escola_id, t.escola_id AS turma_escola_id
            FROM provas p INNER JOIN turmas t ON t.id = p.turma_id
            WHERE p.id = ? LIMIT 1
        """, (prova_id,))
        prova = cursor.fetchone()
        escola_id = prova["escola_id"] or prova["turma_escola_id"]

        cursor.execute("SELECT COALESCE(MAX(ordem), 0) AS ordem FROM prova_questoes WHERE prova_id = ?", (prova_id,))
        ordem = cursor.fetchone()["ordem"]
        adicionadas = 0

        for questao_id in dict.fromkeys(questoes_ids):
            cursor.execute("""
                SELECT id FROM questoes
                WHERE id = ? AND disciplina = ?
                  AND (escola_id = ? OR escola_id IS NULL)
                LIMIT 1
            """, (questao_id, prova["disciplina"], escola_id))
            if not cursor.fetchone():
                continue

            cursor.execute("SELECT 1 FROM prova_questoes WHERE prova_id = ? AND questao_id = ?", (prova_id, questao_id))
            if cursor.fetchone():
                continue

            ordem += 1
            cursor.execute("""
                INSERT INTO prova_questoes (prova_id, questao_id, peso, ordem)
                VALUES (?, ?, 0, ?)
            """, (prova_id, questao_id, ordem))
            adicionadas += 1

        cursor.execute("""
            UPDATE provas SET quantidade = (SELECT COUNT(*) FROM prova_questoes WHERE prova_id = ?),
                atualizado_em = ? WHERE id = ?
        """, (prova_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prova_id))
        banco.commit()

        if adicionadas:
            flash(f"{adicionadas} questão(ões) adicionada(s) à avaliação.", "sucesso")
        else:
            flash("Nenhuma questão nova foi adicionada.", "aviso")
        return redirect(f"/provas/{prova_id}/montar")
    except sqlite3.Error as erro:
        banco.rollback()
        flash(f"Não foi possível adicionar as questões: {erro}", "erro")
        return redirect(f"/provas/{prova_id}/banco-questoes")
    finally:
        banco.close()


@app.route(
    "/provas/<int:prova_id>/questoes/adicionar",
    methods=["POST"]
)
def adicionar_questao_prova(prova_id):
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=True
        ):
            return _redirecionar_acesso_negado_prova()

        try:
            questao_id = int(
                request.form.get("questao_id", "")
            )
        except (TypeError, ValueError):
            flash("Selecione uma questão válida.", "erro")
            return redirect(f"/provas/{prova_id}/montar")

        cursor.execute("""
            SELECT
                p.escola_id,
                p.disciplina,
                t.escola_id AS turma_escola_id
            FROM provas AS p
            INNER JOIN turmas AS t
                ON t.id = p.turma_id
            WHERE p.id = ?
            LIMIT 1
        """, (prova_id,))
        prova = cursor.fetchone()

        cursor.execute("""
            SELECT id, escola_id, disciplina
            FROM questoes
            WHERE id = ?
            LIMIT 1
        """, (questao_id,))
        questao = cursor.fetchone()

        if not prova or not questao:
            flash("Questão não encontrada.", "erro")
            return redirect(f"/provas/{prova_id}/montar")

        escola_prova = (
            prova["escola_id"]
            or prova["turma_escola_id"]
        )

        if (
            questao["escola_id"] is not None
            and int(questao["escola_id"]) != int(escola_prova)
        ):
            return _redirecionar_acesso_negado_prova()

        if (
            (questao["disciplina"] or "").strip().lower()
            != (prova["disciplina"] or "").strip().lower()
        ):
            flash(
                "A questão não pertence ao componente da avaliação.",
                "erro"
            )
            return redirect(f"/provas/{prova_id}/montar")

        cursor.execute("""
            SELECT id
            FROM prova_questoes
            WHERE prova_id = ?
              AND questao_id = ?
            LIMIT 1
        """, (prova_id, questao_id))

        if cursor.fetchone():
            flash(
                "Essa questão já foi adicionada à avaliação.",
                "aviso"
            )
            return redirect(f"/provas/{prova_id}/montar")

        cursor.execute("""
            SELECT COALESCE(MAX(ordem), 0) + 1 AS proxima_ordem
            FROM prova_questoes
            WHERE prova_id = ?
        """, (prova_id,))
        proxima_ordem = cursor.fetchone()["proxima_ordem"]

        cursor.execute("""
            INSERT INTO prova_questoes (
                prova_id,
                questao_id,
                peso,
                ordem
            )
            VALUES (?, ?, 0, ?)
        """, (prova_id, questao_id, proxima_ordem))

        cursor.execute("""
            UPDATE provas
            SET
                quantidade = (
                    SELECT COUNT(*)
                    FROM prova_questoes
                    WHERE prova_id = ?
                ),
                atualizado_em = ?
            WHERE id = ?
        """, (
            prova_id,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            prova_id
        ))

        banco.commit()
        flash("Questão adicionada à avaliação.", "sucesso")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(
            f"Não foi possível adicionar a questão: {erro}",
            "erro"
        )

    finally:
        banco.close()

    return redirect(f"/provas/{prova_id}/montar")




@app.route(
    "/provas/<int:prova_id>/pesos",
    methods=["POST"]
)
def salvar_pesos_prova(prova_id):
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=True
        ):
            return _redirecionar_acesso_negado_prova()

        tipo_peso = (request.form.get("tipo_peso") or "automatico").strip()
        if tipo_peso not in {"automatico", "manual"}:
            tipo_peso = "automatico"

        try:
            peso_total = float(
                (request.form.get("peso_total") or "10").replace(",", ".")
            )
        except (TypeError, ValueError):
            peso_total = 10.0

        if peso_total <= 0:
            flash("O peso total deve ser maior que zero.", "erro")
            return redirect(f"/provas/{prova_id}/montar")

        cursor.execute("""
            SELECT id
            FROM prova_questoes
            WHERE prova_id = ?
            ORDER BY ordem, id
        """, (prova_id,))
        vinculos = cursor.fetchall()

        if not vinculos:
            flash("Adicione questões antes de configurar os pesos.", "aviso")
            return redirect(f"/provas/{prova_id}/montar")

        if tipo_peso == "automatico":
            quantidade = len(vinculos)
            valor_base = round(peso_total / quantidade, 2)
            acumulado = 0.0

            for indice, vinculo in enumerate(vinculos):
                if indice == quantidade - 1:
                    peso = round(peso_total - acumulado, 2)
                else:
                    peso = valor_base
                    acumulado = round(acumulado + peso, 2)

                cursor.execute("""
                    UPDATE prova_questoes
                    SET peso = ?
                    WHERE id = ? AND prova_id = ?
                """, (peso, vinculo["id"], prova_id))

        else:
            soma = 0.0
            pesos = []

            for vinculo in vinculos:
                bruto = request.form.get(f"peso_{vinculo['id']}", "0")
                try:
                    peso = float(str(bruto).replace(",", "."))
                except (TypeError, ValueError):
                    peso = 0.0

                if peso < 0:
                    flash("Os pesos não podem ser negativos.", "erro")
                    return redirect(f"/provas/{prova_id}/montar")

                peso = round(peso, 2)
                pesos.append((peso, vinculo["id"]))
                soma = round(soma + peso, 2)

            if abs(soma - peso_total) > 0.009:
                flash(
                    f"A soma dos pesos ({soma:.2f}) precisa ser igual ao peso total ({peso_total:.2f}).",
                    "erro"
                )
                return redirect(f"/provas/{prova_id}/montar")

            for peso, vinculo_id in pesos:
                cursor.execute("""
                    UPDATE prova_questoes
                    SET peso = ?
                    WHERE id = ? AND prova_id = ?
                """, (peso, vinculo_id, prova_id))

        cursor.execute("""
            UPDATE provas
            SET peso_total = ?,
                tipo_peso = ?,
                atualizado_em = ?
            WHERE id = ?
        """, (
            round(peso_total, 2),
            tipo_peso,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            prova_id
        ))

        banco.commit()
        flash("Pontuação da avaliação salva com sucesso.", "sucesso")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(f"Não foi possível salvar os pesos: {erro}", "erro")

    finally:
        banco.close()

    return redirect(f"/provas/{prova_id}/montar")




@app.route(
    "/provas/<int:prova_id>/questoes/<int:vinculo_id>/duplicar",
    methods=["POST"]
)
def duplicar_questao_prova(prova_id, vinculo_id):
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=True
        ):
            return _redirecionar_acesso_negado_prova()

        cursor.execute("""
            SELECT q.*
            FROM prova_questoes AS pq
            INNER JOIN questoes AS q
                ON q.id = pq.questao_id
            WHERE pq.id = ?
              AND pq.prova_id = ?
            LIMIT 1
        """, (vinculo_id, prova_id))

        original = cursor.fetchone()

        if not original:
            flash("Questão não encontrada na avaliação.", "erro")
            return redirect(f"/provas/{prova_id}/montar")

        # Duplica todas as colunas existentes da questão, exceto o ID.
        # Os campos de criação/atualização são ajustados para o novo registro.
        dados = dict(original)
        dados.pop("id", None)

        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if "criado_por" in dados:
            dados["criado_por"] = session.get("usuario_id")

        if "criado_em" in dados:
            dados["criado_em"] = agora

        if "atualizado_em" in dados:
            dados["atualizado_em"] = agora

        colunas = list(dados.keys())
        marcadores = ", ".join(["?"] * len(colunas))
        nomes_colunas = ", ".join(colunas)

        cursor.execute(
            f"""
            INSERT INTO questoes ({nomes_colunas})
            VALUES ({marcadores})
            """,
            [dados[coluna] for coluna in colunas]
        )

        nova_questao_id = cursor.lastrowid

        cursor.execute("""
            SELECT COALESCE(MAX(ordem), 0) + 1 AS proxima_ordem
            FROM prova_questoes
            WHERE prova_id = ?
        """, (prova_id,))

        proxima_ordem = cursor.fetchone()["proxima_ordem"]

        cursor.execute("""
            INSERT INTO prova_questoes (
                prova_id,
                questao_id,
                peso,
                ordem
            )
            VALUES (?, ?, 0, ?)
        """, (
            prova_id,
            nova_questao_id,
            proxima_ordem
        ))

        cursor.execute("""
            UPDATE provas
            SET quantidade = (
                    SELECT COUNT(*)
                    FROM prova_questoes
                    WHERE prova_id = ?
                ),
                atualizado_em = ?
            WHERE id = ?
        """, (
            prova_id,
            agora,
            prova_id
        ))

        banco.commit()
        flash(
            "Questão duplicada e adicionada ao final da avaliação.",
            "sucesso"
        )

    except sqlite3.Error as erro:
        banco.rollback()
        flash(
            f"Não foi possível duplicar a questão: {erro}",
            "erro"
        )

    finally:
        banco.close()

    return redirect(f"/provas/{prova_id}/montar")


@app.route(
    "/provas/<int:prova_id>/questoes/<int:vinculo_id>/mover/<direcao>",
    methods=["POST"]
)
def mover_questao_prova(prova_id, vinculo_id, direcao):
    if direcao not in {"cima", "baixo"}:
        flash("Direção de movimentação inválida.", "erro")
        return redirect(f"/provas/{prova_id}/montar")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=True):
            return _redirecionar_acesso_negado_prova()

        normalizar_ordem_questoes_prova(cursor, prova_id)

        cursor.execute("""
            SELECT id, ordem
            FROM prova_questoes
            WHERE id = ? AND prova_id = ?
            LIMIT 1
        """, (vinculo_id, prova_id))
        atual = cursor.fetchone()

        if not atual:
            flash("Questão não encontrada na avaliação.", "erro")
            return redirect(f"/provas/{prova_id}/montar")

        operador = "<" if direcao == "cima" else ">"
        ordenacao = "DESC" if direcao == "cima" else "ASC"

        cursor.execute(f"""
            SELECT id, ordem
            FROM prova_questoes
            WHERE prova_id = ? AND ordem {operador} ?
            ORDER BY ordem {ordenacao}, id {ordenacao}
            LIMIT 1
        """, (prova_id, atual["ordem"]))
        vizinha = cursor.fetchone()

        if vizinha:
            cursor.execute("""
                UPDATE prova_questoes SET ordem = ?
                WHERE id = ? AND prova_id = ?
            """, (vizinha["ordem"], atual["id"], prova_id))
            cursor.execute("""
                UPDATE prova_questoes SET ordem = ?
                WHERE id = ? AND prova_id = ?
            """, (atual["ordem"], vizinha["id"], prova_id))
            cursor.execute("""
                UPDATE provas SET atualizado_em = ? WHERE id = ?
            """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prova_id))
            banco.commit()
            flash("Ordem das questões atualizada.", "sucesso")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(f"Não foi possível reordenar a questão: {erro}", "erro")
    finally:
        banco.close()

    return redirect(f"/provas/{prova_id}/montar")


@app.route(
    "/provas/<int:prova_id>/questoes/<int:vinculo_id>/remover",
    methods=["POST"]
)
def remover_questao_prova(prova_id, vinculo_id):
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=True
        ):
            return _redirecionar_acesso_negado_prova()

        cursor.execute("""
            DELETE FROM prova_questoes
            WHERE id = ?
              AND prova_id = ?
        """, (vinculo_id, prova_id))

        normalizar_ordem_questoes_prova(cursor, prova_id)

        cursor.execute("""
            UPDATE provas
            SET
                quantidade = (
                    SELECT COUNT(*)
                    FROM prova_questoes
                    WHERE prova_id = ?
                ),
                atualizado_em = ?
            WHERE id = ?
        """, (
            prova_id,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            prova_id
        ))

        banco.commit()
        flash("Questão removida da avaliação.", "sucesso")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(
            f"Não foi possível remover a questão: {erro}",
            "erro"
        )

    finally:
        banco.close()

    return redirect(f"/provas/{prova_id}/montar")


@app.route(
    "/provas/<int:prova_id>/finalizar",
    methods=["POST"]
)
def finalizar_prova(prova_id):
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=True
        ):
            return _redirecionar_acesso_negado_prova()

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM prova_questoes
            WHERE prova_id = ?
        """, (prova_id,))
        total = cursor.fetchone()["total"]

        if total <= 0:
            flash(
                "Adicione pelo menos uma questão antes de finalizar.",
                "aviso"
            )
            return redirect(f"/provas/{prova_id}/montar")

        cursor.execute("""
            UPDATE provas
            SET
                quantidade = ?,
                status = 'finalizada',
                atualizado_em = ?
            WHERE id = ?
        """, (
            total,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            prova_id
        ))

        banco.commit()
        flash("Avaliação finalizada com sucesso.", "sucesso")

        return redirect(f"/prova/{prova_id}")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(
            f"Não foi possível finalizar a avaliação: {erro}",
            "erro"
        )
        return redirect(f"/provas/{prova_id}/montar")

    finally:
        banco.close()


@app.route("/prova/<int:prova_id>")
def visualizar_prova(prova_id):
    if not permissao_modulo("Provas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=False
        ):
            return _redirecionar_acesso_negado_prova()

        cursor.execute("""
            SELECT
                p.id,
                p.nome,
                t.nome AS turma_nome,
                p.disciplina,
                COUNT(pq.id) AS quantidade,
                COALESCE(pr.nome, 'Não informado') AS professor_nome,
                p.data_geracao,
                p.data_aplicacao,
                e.nome_instituicao,
                e.cidade,
                e.estado,
                e.logo
            FROM provas AS p
            INNER JOIN turmas AS t
                ON t.id = p.turma_id
            INNER JOIN escolas AS e
                ON e.id = COALESCE(p.escola_id, t.escola_id)
            LEFT JOIN professores AS pr
                ON pr.id = p.professor_id
            LEFT JOIN prova_questoes AS pq
                ON pq.prova_id = p.id
            WHERE p.id = ?
            GROUP BY
                p.id,
                p.nome,
                t.nome,
                p.disciplina,
                pr.nome,
                p.data_geracao,
                p.data_aplicacao,
                e.nome_instituicao,
                e.cidade,
                e.estado,
                e.logo
            LIMIT 1
        """, (prova_id,))

        prova = cursor.fetchone()

        if prova is None:
            flash("Avaliação não encontrada.", "erro")
            return redirect("/provas")

        cursor.execute("""
            SELECT q.*, COALESCE(pq.peso, 0) AS peso
            FROM prova_questoes AS pq
            INNER JOIN questoes AS q
                ON q.id = pq.questao_id
            WHERE pq.prova_id = ?
            ORDER BY COALESCE(NULLIF(pq.ordem, 0), pq.id), pq.id
        """, (prova_id,))

        questoes = cursor.fetchall()

        instituicao = {
            "nome": prova["nome_instituicao"] or "ARK EDUS",
            "cidade": prova["cidade"] or "Não informada",
            "estado": prova["estado"] or "Não informado",
            "logo": prova["logo"]
        }

        return render_template(
            "visualizar_prova.html",
            prova=prova,
            questoes=questoes,
            instituicao=instituicao
        )

    except sqlite3.Error as erro:
        flash(
            f"Não foi possível abrir a avaliação: {erro}",
            "erro"
        )
        return redirect("/provas")

    finally:
        banco.close()

@app.route("/cartao_resposta/<int:prova_id>")
def cartao_resposta(prova_id):

    if "usuario_id" not in session:
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT 
            provas.id,
            provas.nome,
            turmas.nome,
            provas.disciplina,
            professores.nome,
            provas.turma_id,
            provas.data_aplicacao
        FROM provas
        JOIN turmas
            ON provas.turma_id = turmas.id
        LEFT JOIN professores
            ON provas.professor_id = professores.id
        WHERE provas.id = ?
    """, (prova_id,))

    prova = cursor.fetchone()

    if not prova:
        banco.close()
        return redirect("/provas")

    cursor.execute("""
        SELECT COUNT(*)
        FROM prova_questoes
        WHERE prova_id = ?
    """, (prova_id,))

    quantidade = cursor.fetchone()[0]

    cursor.execute("""
        SELECT id, nome, matricula
        FROM alunos
        WHERE turma_id = ?
        ORDER BY nome
    """, (prova[5],))

    alunos = cursor.fetchall()

    cursor.execute("""
        SELECT *
        FROM instituicao
        WHERE id = 1
    """)

    instituicao = cursor.fetchone()

    banco.close()

    cartoes = []

    for aluno in alunos:

        codigo_qr = f"PROVA:{prova[0]}|ALUNO:{aluno[0]}|TURMA:{prova[2]}"

        qr = qrcode.make(codigo_qr)

        buffer = BytesIO()

        qr.save(buffer, format="PNG")

        qr_base64 = base64.b64encode(
            buffer.getvalue()
        ).decode("utf-8")

        cartoes.append({
            "aluno": aluno,
            "qr_base64": qr_base64
        })

    return render_template(
        "cartao_resposta.html",
        prova=prova,
        quantidade=quantidade,
        instituicao=instituicao,
        cartoes=cartoes
    )

@app.route("/instituicao")
def instituicao():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT *
        FROM instituicao
        WHERE id = 1
    """)

    dados = cursor.fetchone()

    banco.close()

    return render_template(
        "instituicao.html",
        instituicao=dados
    )

@app.route("/salvar_instituicao", methods=["POST"])
def salvar_instituicao():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    nome = request.form["nome"]
    cidade = request.form["cidade"]
    estado = request.form["estado"]
    diretor = request.form["diretor"]
    coordenador = request.form["coordenador"]
    ano_letivo = request.form["ano_letivo"]

    logo = request.files.get("logo")
    nome_logo = ""

    if logo and logo.filename != "":
        nome_logo = secure_filename(logo.filename)

        logo.save(
            os.path.join(
                app.config["UPLOAD_FOLDER"],
                nome_logo
            )
        )

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "SELECT * FROM instituicao WHERE id = 1"
    )

    existe = cursor.fetchone()

    if existe:

        if nome_logo:

            cursor.execute("""
                UPDATE instituicao
                SET nome = ?,
                    cidade = ?,
                    estado = ?,
                    diretor = ?,
                    coordenador = ?,
                    ano_letivo = ?,
                    logo = ?
                WHERE id = 1
            """, (
                nome,
                cidade,
                estado,
                diretor,
                coordenador,
                ano_letivo,
                nome_logo
            ))

        else:

            cursor.execute("""
                UPDATE instituicao
                SET nome = ?,
                    cidade = ?,
                    estado = ?,
                    diretor = ?,
                    coordenador = ?,
                    ano_letivo = ?
                WHERE id = 1
            """, (
                nome,
                cidade,
                estado,
                diretor,
                coordenador,
                ano_letivo
            ))

    else:

        cursor.execute("""
            INSERT INTO instituicao (
                id,
                nome,
                cidade,
                estado,
                diretor,
                coordenador,
                ano_letivo,
                logo
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nome,
            cidade,
            estado,
            diretor,
            coordenador,
            ano_letivo,
            nome_logo
        ))

    banco.commit()
    banco.close()

    return redirect("/instituicao")

@app.route(
    "/excluir_prova/<int:prova_id>",
    methods=["GET", "POST"]
)
def excluir_prova(prova_id):
    """Exclui uma avaliação após validar sessão, módulo e vínculo do usuário."""

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    ]):
        return redirect("/login")

    if not permissao_modulo("Provas"):
        return redirect("/acesso_negado")

    cargo = (session.get("usuario_cargo") or "").strip()

    if cargo not in {
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    }:
        flash(
            "Você não possui permissão para excluir avaliações.",
            "erro"
        )
        return redirect("/provas")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        # Usa a mesma validação de instituição, ano letivo e professor
        # aplicada nas demais ações do módulo de avaliações.
        if not _pode_gerenciar_prova(
            cursor,
            prova_id,
            exigir_edicao=False
        ):
            flash(
                "Avaliação não encontrada ou você não possui permissão para excluí-la.",
                "erro"
            )
            return redirect("/provas")

        cursor.execute("""
            SELECT nome
            FROM provas
            WHERE id = ?
            LIMIT 1
        """, (prova_id,))

        prova = cursor.fetchone()

        if prova is None:
            flash("Avaliação não encontrada.", "erro")
            return redirect("/provas")

        # O projeto não ativa PRAGMA foreign_keys em todas as conexões.
        # Por isso, os registros dependentes são removidos explicitamente.
        cursor.execute(
            "DELETE FROM respostas_alunos WHERE prova_id = ?",
            (prova_id,)
        )

        cursor.execute(
            "DELETE FROM resultados WHERE prova_id = ?",
            (prova_id,)
        )

        cursor.execute(
            "DELETE FROM prova_questoes WHERE prova_id = ?",
            (prova_id,)
        )

        cursor.execute(
            "DELETE FROM provas WHERE id = ?",
            (prova_id,)
        )

        if cursor.rowcount == 0:
            banco.rollback()
            flash("A avaliação não foi encontrada.", "erro")
            return redirect("/provas")

        banco.commit()

        flash(
            f'A avaliação “{prova["nome"]}” foi excluída com sucesso.',
            "sucesso"
        )

    except sqlite3.Error as erro:
        banco.rollback()
        print("ERRO AO EXCLUIR AVALIAÇÃO:", erro)
        flash(
            f"Não foi possível excluir a avaliação: {erro}",
            "erro"
        )

    finally:
        banco.close()

    return redirect("/provas")



@app.route("/editar_prova/<int:prova_id>")
def editar_prova(prova_id):
    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cursor.execute("SELECT status FROM provas WHERE id = ?", (prova_id,))
    status_prova = cursor.fetchone()
    if status_prova and (status_prova["status"] or "rascunho").lower() == "finalizada":
        banco.close()
        flash("Esta avaliação já foi finalizada e não pode mais ser editada.", "aviso")
        return redirect(f"/provas/{prova_id}/montar")

    if not _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=True):
        banco.close()
        return _redirecionar_acesso_negado_prova()

    cursor.execute("SELECT * FROM provas WHERE id = ?", (prova_id,))
    prova = cursor.fetchone()

    cursor.execute("""
        SELECT * FROM turmas
        WHERE escola_id = ? AND ano_letivo_id = ?
        ORDER BY nome
    """, (prova["escola_id"], prova["ano_letivo_id"]))
    turmas = cursor.fetchall()

    cursor.execute("""
        SELECT * FROM professores
        WHERE escola_id = ?
        ORDER BY nome
    """, (prova["escola_id"],))
    professores = cursor.fetchall()

    banco.close()

    return render_template(
        "editar_prova.html",
        prova=prova,
        turmas=turmas,
        professores=professores
    )


@app.route("/atualizar_prova/<int:prova_id>", methods=["POST"])
def atualizar_prova(prova_id):
    nome = request.form.get("nome", "").strip()
    disciplina = request.form.get("disciplina", "").strip()
    data_aplicacao = request.form.get("data_aplicacao", "").strip()
    media_ativa = 1 if request.form.get("media_ativa") == "1" else 0
    media_aprovacao = None

    if not nome or not disciplina:
        flash("Preencha os dados obrigatórios da avaliação.", "erro")
        return redirect(f"/editar_prova/{prova_id}")

    try:
        turma_id = int(request.form.get("turma_id", ""))
        professor_id = int(request.form.get("professor_id", ""))
    except (TypeError, ValueError):
        flash("Selecione uma turma e um professor válidos.", "erro")
        return redirect(f"/editar_prova/{prova_id}")

    if media_ativa:
        try:
            media_aprovacao = float(
                request.form.get("media_aprovacao", "").strip().replace(",", ".")
            )
        except (TypeError, ValueError):
            flash("Informe uma média válida entre 0,0 e 10,0.", "erro")
            return redirect(f"/editar_prova/{prova_id}")

        if not 0 <= media_aprovacao <= 10:
            flash("A média deve estar entre 0,0 e 10,0.", "erro")
            return redirect(f"/editar_prova/{prova_id}")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if not _pode_gerenciar_prova(cursor, prova_id, exigir_edicao=True):
            return _redirecionar_acesso_negado_prova()

        cursor.execute("SELECT escola_id, ano_letivo_id FROM provas WHERE id = ?", (prova_id,))
        prova_atual = cursor.fetchone()

        cursor.execute("""
            SELECT 1 FROM turmas
            WHERE id = ? AND escola_id = ? AND ano_letivo_id = ?
        """, (turma_id, prova_atual["escola_id"], prova_atual["ano_letivo_id"]))
        if not cursor.fetchone():
            flash("A turma selecionada não pertence à avaliação.", "erro")
            return redirect(f"/editar_prova/{prova_id}")

        cursor.execute("""
            SELECT 1 FROM professores
            WHERE id = ? AND escola_id = ?
        """, (professor_id, prova_atual["escola_id"]))
        if not cursor.fetchone():
            flash("O professor selecionado não pertence à instituição.", "erro")
            return redirect(f"/editar_prova/{prova_id}")

        cursor.execute("""
            UPDATE provas
            SET nome = ?, turma_id = ?, professor_id = ?, disciplina = ?,
                data_aplicacao = ?, media_ativa = ?, media_aprovacao = ?,
                atualizado_em = ?
            WHERE id = ?
        """, (
            nome, turma_id, professor_id, disciplina, data_aplicacao,
            media_ativa, media_aprovacao,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prova_id
        ))
        banco.commit()
        flash("Avaliação atualizada com sucesso.", "sucesso")
        return redirect("/provas")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(f"Erro ao atualizar a avaliação: {erro}", "erro")
        return redirect(f"/editar_prova/{prova_id}")
    finally:
        banco.close()

# ==========================
# EXCLUIR PROFESSOR
# ==========================

@app.route("/excluir_professor/<int:professor_id>")
def excluir_professor(professor_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "DELETE FROM professor_disciplinas WHERE professor_id = ?",
        (professor_id,)
    )

    cursor.execute(
        "DELETE FROM professor_turmas WHERE professor_id = ?",
        (professor_id,)
    )

    cursor.execute(
        "UPDATE provas SET professor_id = NULL WHERE professor_id = ?",
        (professor_id,)
    )

    cursor.execute(
        "DELETE FROM professores WHERE id = ?",
        (professor_id,)
    )

    banco.commit()
    banco.close()

    return redirect("/professores")

# ==========================
# TELA EDITAR PROFESSOR
# ==========================

@app.route("/editar_professor/<int:professor_id>")
def editar_professor(professor_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "SELECT * FROM professores WHERE id = ?",
        (professor_id,)
    )
    professor = cursor.fetchone()

    cursor.execute(
        "SELECT disciplina FROM professor_disciplinas WHERE professor_id = ?",
        (professor_id,)
    )
    disciplinas = [d[0] for d in cursor.fetchall()]

    cursor.execute(
        "SELECT turma_id FROM professor_turmas WHERE professor_id = ?",
        (professor_id,)
    )
    turmas_vinculadas = [t[0] for t in cursor.fetchall()]

    cursor.execute("SELECT * FROM turmas ORDER BY nome")
    turmas = cursor.fetchall()

    banco.close()

    return render_template(
        "editar_professor.html",
        professor=professor,
        disciplinas=disciplinas,
        turmas=turmas,
        turmas_vinculadas=turmas_vinculadas
    )

# ==========================
# SALVAR EDIÇÃO PROFESSOR
# ==========================

@app.route("/atualizar_professor/<int:professor_id>", methods=["POST"])
def atualizar_professor(professor_id):

    nome = request.form["nome"]
    email = request.form["email"]

    disciplinas = request.form.getlist("disciplinas")
    turmas = request.form.getlist("turmas")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        UPDATE professores
        SET nome = ?, email = ?, disciplina = ?
        WHERE id = ?
    """, (
        nome,
        email,
        ", ".join(disciplinas),
        professor_id
    ))

    cursor.execute(
        "DELETE FROM professor_disciplinas WHERE professor_id = ?",
        (professor_id,)
    )

    for disciplina in disciplinas:
        cursor.execute("""
            INSERT INTO professor_disciplinas
            (professor_id, disciplina)
            VALUES (?, ?)
        """, (professor_id, disciplina))

    cursor.execute(
        "DELETE FROM professor_turmas WHERE professor_id = ?",
        (professor_id,)
    )

    for turma_id in turmas:
        cursor.execute("""
            INSERT INTO professor_turmas
            (professor_id, turma_id)
            VALUES (?, ?)
        """, (professor_id, turma_id))

    banco.commit()
    banco.close()

    return redirect("/professores")

# ==========================
# EXCLUIR ALUNO
# ==========================

@app.route("/excluir_aluno/<int:aluno_id>")
def excluir_aluno(aluno_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "DELETE FROM alunos WHERE id = ?",
        (aluno_id,)
    )

    banco.commit()
    banco.close()

    return redirect("/alunos")

# ==========================
# TELA EDITAR ALUNO
# ==========================

@app.route("/editar_aluno/<int:aluno_id>")
def editar_aluno(aluno_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "SELECT * FROM alunos WHERE id = ?",
        (aluno_id,)
    )
    aluno = cursor.fetchone()

    cursor.execute(
        "SELECT * FROM turmas ORDER BY nome"
    )
    turmas = cursor.fetchall()

    banco.close()

    return render_template(
        "editar_aluno.html",
        aluno=aluno,
        turmas=turmas
    )

# ==========================
# SALVAR EDIÇÃO ALUNO
# ==========================

@app.route("/atualizar_aluno/<int:aluno_id>", methods=["POST"])
def atualizar_aluno(aluno_id):

    nome = request.form["nome"]
    matricula = request.form["matricula"]
    turma_id = request.form["turma_id"]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        UPDATE alunos
        SET nome = ?, matricula = ?, turma_id = ?
        WHERE id = ?
    """, (
        nome,
        matricula,
        turma_id,
        aluno_id
    ))

    banco.commit()
    banco.close()

    return redirect("/alunos")

# ==========================
# ATUALIZAR TURMA
# ==========================

@app.route("/atualizar_turma/<int:turma_id>", methods=["POST"])
def atualizar_turma(turma_id):

    nome = request.form["nome"]
    ano = request.form["ano"]
    turno = request.form["turno"]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        UPDATE turmas
        SET nome = ?, ano = ?, turno = ?
        WHERE id = ?
    """, (
        nome,
        ano,
        turno,
        turma_id
    ))

    banco.commit()
    banco.close()

    return redirect("/turmas")


# ==========================
# ALUNOS DA TURMA
# ==========================

@app.route("/turma/<int:turma_id>/alunos")
def alunos_turma(turma_id):

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "SELECT * FROM turmas WHERE id = ?",
        (turma_id,)
    )

    turma = cursor.fetchone()

    cursor.execute("""
        SELECT id, nome, matricula
        FROM alunos
        WHERE turma_id = ?
        ORDER BY nome
    """, (turma_id,))

    alunos = cursor.fetchall()

    banco.close()

    return render_template(
        "alunos_turma.html",
        turma=turma,
        alunos=alunos
    )

@app.route("/turma_professores/<int:turma_id>")
def turma_professores(turma_id):

    banco = conectar_banco()
    cursor = banco.cursor()

    # Dados da turma
    cursor.execute("""
        SELECT *
        FROM turmas
        WHERE id = ?
    """, (turma_id,))

    turma = cursor.fetchone()

    # Professores vinculados
    cursor.execute("""
        SELECT
            professores.nome,
            professores.email,
            professores.disciplina
        FROM professor_turmas
        JOIN professores
            ON professor_turmas.professor_id = professores.id
        WHERE professor_turmas.turma_id = ?
    """, (turma_id,))

    professores = cursor.fetchall()

    banco.close()

    return render_template(
        "turma_professores.html",
        turma=turma,
        professores=professores
    )

# =====================================
# SELEÇÃO MANUAL DE QUESTÕES
# =====================================

@app.route("/selecionar_questoes")
def selecionar_questoes():

    professor_id = request.args.get("professor_id", "")
    disciplina = request.args.get("disciplina", "")
    habilidade = request.args.get("habilidade", "")
    descritor = request.args.get("descritor", "")

    banco = conectar_banco()
    cursor = banco.cursor()

    sql = """
        SELECT *
        FROM questoes
        WHERE 1=1
    """

    parametros = []

    if disciplina:
        sql += " AND disciplina = ? "
        parametros.append(disciplina)

    if habilidade:
        sql += " AND habilidade LIKE ? "
        parametros.append(f"%{habilidade}%")

    if descritor:
        sql += " AND enunciado LIKE ? "
        parametros.append(f"%{descritor}%")

    sql += " ORDER BY disciplina, id "

    cursor.execute(sql, parametros)
    questoes = cursor.fetchall()

    cursor.execute("SELECT * FROM turmas")
    turmas = cursor.fetchall()

    cursor.execute("""
        SELECT
            professores.id,
            professores.nome,
            professores.email,
            professores.disciplina
        FROM professores
        ORDER BY professores.nome
    """)
    professores = cursor.fetchall()

    banco.close()

    return render_template(
        "selecionar_questoes.html",
        questoes=questoes,
        turmas=turmas,
        professores=professores,
        professor_id=professor_id,
        disciplina=disciplina,
        habilidade=habilidade,
        descritor=descritor
    )


@app.route("/gerar_prova_manual", methods=["POST"])
def gerar_prova_manual():

    nome = request.form["nome"]
    turma_id = request.form["turma_id"]
    professor_id = request.form["professor_id"]
    disciplina = request.form["disciplina"]

    questoes_selecionadas = request.form.getlist("questoes")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        INSERT INTO provas
        (nome, turma_id, professor_id, disciplina, quantidade)
        VALUES (?, ?, ?, ?, ?)
    """, (
        nome,
        turma_id,
        professor_id,
        disciplina,
        len(questoes_selecionadas)
    ))

    prova_id = cursor.lastrowid

    for questao_id in questoes_selecionadas:
        cursor.execute("""
            INSERT INTO prova_questoes
            (prova_id, questao_id)
            VALUES (?, ?)
        """, (
            prova_id,
            questao_id
        ))

    banco.commit()
    banco.close()

    return redirect("/provas")

@app.route("/importar_cartoes/<int:prova_id>")
def importar_cartoes(prova_id):

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT
            provas.id,
            provas.nome,
            turmas.nome
        FROM provas
        JOIN turmas ON provas.turma_id = turmas.id
        WHERE provas.id = ?
    """, (prova_id,))

    prova = cursor.fetchone()

    banco.close()

    return render_template(
        "importar_cartoes.html",
        prova=prova
    )

def ler_respostas_cartao(caminho_imagem, quantidade):
    imagem = cv2.imread(caminho_imagem)

    if imagem is None:
        return {}

    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(cinza, (5, 5), 0)

    _, thresh = cv2.threshold(
        blur, 0, 255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    contornos, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    marcadores = []

    for c in contornos:
        area = cv2.contourArea(c)
        x, y, w, h = cv2.boundingRect(c)

        if area > 250 and area < 2000:
            proporcao = w / float(h)

            if 0.75 <= proporcao <= 1.25:
                marcadores.append((x, y, w, h))

    if len(marcadores) < 4:
        return {}

    marcadores = sorted(marcadores, key=lambda m: m[2] * m[3], reverse=True)[:4]

    pontos = []

    for x, y, w, h in marcadores:
        pontos.append([x + w // 2, y + h // 2])

    pontos = np.array(pontos, dtype="float32")

    soma = pontos.sum(axis=1)
    diferenca = np.diff(pontos, axis=1)

    ordenados = np.zeros((4, 2), dtype="float32")
    ordenados[0] = pontos[np.argmin(soma)]
    ordenados[2] = pontos[np.argmax(soma)]
    ordenados[1] = pontos[np.argmin(diferenca)]
    ordenados[3] = pontos[np.argmax(diferenca)]

    largura = 900
    altura = 350

    destino = np.array([
        [0, 0],
        [largura - 1, 0],
        [largura - 1, altura - 1],
        [0, altura - 1]
    ], dtype="float32")

    matriz = cv2.getPerspectiveTransform(ordenados, destino)
    recorte = cv2.warpPerspective(imagem, matriz, (largura, altura))

    cinza = cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(cinza, (5, 5), 0)

    _, thresh = cv2.threshold(
        blur, 0, 255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    contornos, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    bolhas = []

    for c in contornos:
        x, y, w, h = cv2.boundingRect(c)
        area = cv2.contourArea(c)

        if 12 <= w <= 35 and 12 <= h <= 35 and area > 80:
            proporcao = w / float(h)

            if 0.75 <= proporcao <= 1.25:
                bolhas.append((x, y, w, h))

    respostas = {}
    alternativas = ["A", "B", "C", "D"]

    colunas = 1

    if quantidade > 40:
        colunas = 3
    elif quantidade > 20:
        colunas = 2

    largura_coluna = largura // colunas

    for coluna in range(colunas):

        inicio_x = coluna * largura_coluna
        fim_x = inicio_x + largura_coluna

        bolhas_coluna = [
            b for b in bolhas
            if inicio_x < b[0] < fim_x
        ]

        bolhas_coluna = sorted(bolhas_coluna, key=lambda b: b[1])

        linhas = []

        for bolha in bolhas_coluna:
            x, y, w, h = bolha
            adicionou = False

            for linha in linhas:
                if abs(linha[0][1] - y) < 15:
                    linha.append(bolha)
                    adicionou = True
                    break

            if not adicionou:
                linhas.append([bolha])

        linhas = sorted(linhas, key=lambda l: l[0][1])

        for indice_linha, linha in enumerate(linhas):

            linha = sorted(linha, key=lambda b: b[0])

            if len(linha) < 4:
                continue

            linha = linha[:4]

            questao = coluna * 20 + indice_linha + 1

            if questao > quantidade:
                continue

            preenchimentos = []

            for x, y, w, h in linha:
                roi = thresh[y:y+h, x:x+w]
                total = cv2.countNonZero(roi)
                preenchimentos.append(total)

            maior = max(preenchimentos)
            indice = preenchimentos.index(maior)

            if maior > 180:
                respostas[questao] = alternativas[indice]

    return respostas

@app.route("/corrigir_cartoes/<int:prova_id>", methods=["POST"])
def corrigir_cartoes(prova_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    ]):
        return redirect("/login")

    if "arquivo" not in request.files:
        return "Nenhum arquivo enviado."

    arquivo = request.files["arquivo"]

    if arquivo.filename == "":
        return "Nenhum arquivo enviado."

    os.makedirs("uploads_cartoes", exist_ok=True)

    nome_arquivo = secure_filename(arquivo.filename)

    caminho = os.path.join(
        "uploads_cartoes",
        nome_arquivo
    )

    arquivo.save(caminho)

    try:
        imagem_pil = Image.open(caminho).convert("RGB")
        imagem = np.array(imagem_pil)

    except Exception as erro:
        print("Erro ao cadastrar usuário:", erro)
        traceback.print_exc()

    codigos = decode(imagem)

    if len(codigos) == 0:
        return """
        <h2>QR Code não encontrado</h2>
        <p>Envie uma foto mais nítida, bem iluminada e sem cortes.</p>
        <a href='/provas'>Voltar para provas</a>
        """

    qr_texto = codigos[0].data.decode("utf-8")

    try:
        partes = qr_texto.split("|")
        prova_qr = int(partes[0].replace("PROVA:", ""))
        aluno_id = int(partes[1].replace("ALUNO:", ""))

    except Exception:
        return f"""
        <h2>Erro ao ler dados do QR Code</h2>
        <p>QR Code lido: {qr_texto}</p>
        <a href='/provas'>Voltar</a>
        """

    if prova_qr != prova_id:
        return """
        <h2>Cartão não pertence a esta prova.</h2>
        <p>Confira se você enviou o cartão-resposta correto.</p>
        <a href='/provas'>Voltar</a>
        """

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM prova_questoes
        WHERE prova_id = ?
    """, (prova_qr,))

    total_questoes = cursor.fetchone()[0]

    respostas_lidas = ler_respostas_cartao(
        caminho,
        total_questoes
    )

    cursor.execute("""
        SELECT questoes.correta
        FROM prova_questoes
        JOIN questoes
            ON prova_questoes.questao_id = questoes.id
        WHERE prova_questoes.prova_id = ?
        ORDER BY COALESCE(NULLIF(prova_questoes.ordem, 0), prova_questoes.id), prova_questoes.id
    """, (prova_qr,))

    gabarito = cursor.fetchall()

    cursor.execute("""
        DELETE FROM respostas_alunos
        WHERE prova_id = ? AND aluno_id = ?
    """, (prova_qr, aluno_id))

    acertos = 0

    for i, g in enumerate(gabarito, start=1):

        correta = g[0]
        resposta_aluno = respostas_lidas.get(i)

        acertou = 0

        if resposta_aluno == correta:
            acertos += 1
            acertou = 1

        cursor.execute("""
            INSERT INTO respostas_alunos
            (
                prova_id,
                aluno_id,
                numero_questao,
                resposta_aluno,
                resposta_correta,
                acertou
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            prova_qr,
            aluno_id,
            i,
            resposta_aluno,
            correta,
            acertou
        ))

    erros = total_questoes - acertos

    if total_questoes > 0:
        nota = round((acertos / total_questoes) * 10, 1)
    else:
        nota = 0

    cursor.execute("""
        DELETE FROM resultados
        WHERE prova_id = ? AND aluno_id = ?
    """, (prova_qr, aluno_id))

    cursor.execute("""
        INSERT INTO resultados
        (
            prova_id,
            aluno_id,
            acertos,
            erros,
            nota
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        prova_qr,
        aluno_id,
        acertos,
        erros,
        nota
    ))

    banco.commit()
    banco.close()

    return f"""
    <h2>Cartão corrigido com sucesso!</h2>

    <p><strong>Prova:</strong> {prova_qr}</p>
    <p><strong>Aluno ID:</strong> {aluno_id}</p>
    <p><strong>Arquivo:</strong> {nome_arquivo}</p>
    <p><strong>Total de questões:</strong> {total_questoes}</p>

    <p><strong>Acertos:</strong> {acertos}</p>
    <p><strong>Erros:</strong> {erros}</p>
    <p><strong>Nota:</strong> {nota}</p>

    <br>

    <a href='/resultados/{prova_qr}'>Ver resultados</a>
    """

@app.route("/resultados/<int:prova_id>")
def resultados(prova_id):

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cursor.execute("""
        SELECT id, nome, media_ativa, media_aprovacao
        FROM provas
        WHERE id = ?
    """, (prova_id,))
    prova = cursor.fetchone()

    if not prova:
        banco.close()
        flash("Avaliação não encontrada.", "erro")
        return redirect("/provas")

    cursor.execute("""
        SELECT
            alunos.nome,
            resultados.acertos,
            resultados.erros,
            resultados.nota
        FROM resultados
        JOIN alunos ON resultados.aluno_id = alunos.id
        WHERE resultados.prova_id = ?
        ORDER BY resultados.nota DESC
    """, (prova_id,))
    lista_resultados = cursor.fetchall()

    cursor.execute("""
        SELECT COUNT(*) AS total, ROUND(AVG(nota), 1) AS media,
               MAX(nota) AS maior, MIN(nota) AS menor
        FROM resultados
        WHERE prova_id = ?
    """, (prova_id,))
    estatisticas = cursor.fetchone()

    total_alunos = estatisticas["total"] or 0
    media_turma = estatisticas["media"] or 0
    maior_nota = estatisticas["maior"] or 0
    menor_nota = estatisticas["menor"] or 0

    media_ativa = bool(prova["media_ativa"])
    media_aprovacao = prova["media_aprovacao"] if media_ativa else None
    aprovados = 0
    reprovados = 0
    taxa_aprovacao = 0

    if media_ativa and media_aprovacao is not None:
        cursor.execute("""
            SELECT
                SUM(CASE WHEN nota >= ? THEN 1 ELSE 0 END) AS aprovados,
                SUM(CASE WHEN nota < ? THEN 1 ELSE 0 END) AS reprovados
            FROM resultados
            WHERE prova_id = ?
        """, (media_aprovacao, media_aprovacao, prova_id))
        contagem = cursor.fetchone()
        aprovados = contagem["aprovados"] or 0
        reprovados = contagem["reprovados"] or 0

        if total_alunos > 0:
            taxa_aprovacao = round((aprovados / total_alunos) * 100, 1)

    banco.close()

    return render_template(
        "resultados.html",
        prova=prova,
        resultados=lista_resultados,
        total_alunos=total_alunos,
        media_turma=media_turma,
        maior_nota=maior_nota,
        menor_nota=menor_nota,
        media_ativa=media_ativa,
        media_aprovacao=media_aprovacao,
        aprovados=aprovados,
        reprovados=reprovados,
        taxa_aprovacao=taxa_aprovacao
    )

@app.route("/questao_relatorio/<int:prova_id>/<int:numero>")
def questao_relatorio(prova_id, numero):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT
            questoes.enunciado,
            questoes.imagem,
            questoes.alternativa_a,
            questoes.alternativa_b,
            questoes.alternativa_c,
            questoes.alternativa_d,
            questoes.correta
        FROM prova_questoes
        JOIN questoes
            ON prova_questoes.questao_id = questoes.id
        WHERE prova_questoes.prova_id = ?
        ORDER BY COALESCE(NULLIF(prova_questoes.ordem, 0), prova_questoes.id), prova_questoes.id
    """, (prova_id,))

    questoes = cursor.fetchall()

    if numero < 1 or numero > len(questoes):
        banco.close()
        return "Questão não encontrada."

    questao = questoes[numero - 1]

    cursor.execute("""
        SELECT COUNT(*), SUM(acertou)
        FROM respostas_alunos
        WHERE prova_id = ?
        AND numero_questao = ?
    """, (prova_id, numero))

    dados = cursor.fetchone()

    respondentes = dados[0] or 0
    acertos = dados[1] or 0
    erros = respondentes - acertos

    banco.close()

    return render_template(
        "questao_relatorio.html",
        numero=numero,
        questao=questao,
        respondentes=respondentes,
        acertos=acertos,
        erros=erros
    )

@app.route("/relatorio_questoes/<int:prova_id>")
def relatorio_questoes(prova_id):

    if not permissao_modulo("Relatorios_questoes"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT nome
        FROM provas
        WHERE id = ?
    """, (prova_id,))

    prova = cursor.fetchone()

    if not prova:
        banco.close()
        return "Prova não encontrada."

    cursor.execute("""
        SELECT
            numero_questao,
            COUNT(*),
            SUM(acertou)
        FROM respostas_alunos
        WHERE prova_id = ?
        GROUP BY numero_questao
        ORDER BY numero_questao
    """, (prova_id,))

    dados = cursor.fetchall()

    relatorio = []

    for questao, total, acertos in dados:

        acertos = acertos or 0
        erros = total - acertos

        percentual = (
            round((acertos / total) * 100, 1)
            if total > 0
            else 0
        )

        relatorio.append((
            questao,
            total,
            acertos,
            erros,
            percentual
        ))

    banco.close()

    return render_template(
        "relatorio_questoes.html",
        prova=prova,
        prova_id=prova_id,
        relatorio=relatorio
    )

# =========================================================
# LISTAR INSTITUIÇÕES
# =========================================================

@app.route("/gestao/instituicoes")
def gestao_instituicoes():

    # Somente o Administrador Geral pode acessar.
    if not cargo_permitido([
        "Administrador Geral"
    ]):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        cursor.execute("""
            SELECT
                escolas.*,

                anos_letivos.id AS ano_letivo_id_ativo,
                anos_letivos.ano AS ano_letivo_ativo,
                anos_letivos.data_inicio AS ano_data_inicio,
                anos_letivos.data_fim AS ano_data_fim,
                anos_letivos.encerrado AS ano_encerrado,

                (
                    SELECT COUNT(*)
                    FROM usuarios
                    WHERE usuarios.escola_id = escolas.id
                      AND usuarios.ativo = 1
                ) AS total_usuarios,

                (
                    SELECT COUNT(*)
                    FROM turmas
                    WHERE turmas.escola_id = escolas.id
                      AND turmas.ano_letivo_id = anos_letivos.id
                ) AS total_turmas_ano_ativo,

                (
                    SELECT COUNT(*)
                    FROM alunos
                    WHERE alunos.escola_id = escolas.id
                      AND alunos.ano_letivo_id = anos_letivos.id
                ) AS total_alunos_ano_ativo,

                (
                    SELECT COUNT(*)
                    FROM provas
                    WHERE provas.escola_id = escolas.id
                      AND provas.ano_letivo_id = anos_letivos.id
                ) AS total_provas_ano_ativo

            FROM escolas

            LEFT JOIN anos_letivos
                ON anos_letivos.escola_id = escolas.id
               AND anos_letivos.ativo = 1
               AND anos_letivos.encerrado = 0

            ORDER BY
                escolas.nome_instituicao COLLATE NOCASE ASC
        """)

        escolas = cursor.fetchall()

        return render_template(
            "gestao/gestao_instituicoes.html",
            escolas=escolas
        )

    except sqlite3.Error as erro:

        import traceback
        traceback.print_exc()

        print(
            "ERRO AO LISTAR INSTITUIÇÕES:",
            erro
        )

        flash(
            f"Erro ao carregar as instituições: {erro}",
            "erro"
        )

        return render_template(
            "gestao/gestao_instituicoes.html",
            escolas=[]
        )

    finally:
        banco.close()

@app.route("/gestao/instituicoes/nova", methods=["GET", "POST"])
def nova_instituicao():

    print("ROTA NOVA INSTITUIÇÃO ACESSADA")
    print("MÉTODO RECEBIDO:", request.method)

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    if request.method == "POST":

        print("FORMULÁRIO RECEBIDO:")
        print(request.form.to_dict(flat=False))

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    if request.method == "POST":

        # =====================================================
        # DADOS DA INSTITUIÇÃO
        # =====================================================

        nome_instituicao = request.form.get(
            "nome_instituicao",
            ""
        ).strip()

        codigo_inep = request.form.get(
            "codigo_inep",
            ""
        ).strip()

        cnpj = request.form.get(
            "cnpj",
            ""
        ).strip()

        cep = request.form.get(
            "cep",
            ""
        ).strip()

        endereco = request.form.get(
            "endereco",
            ""
        ).strip()

        cidade = request.form.get(
            "cidade",
            ""
        ).strip()

        estado = request.form.get(
            "estado",
            ""
        ).strip()

        telefone = request.form.get(
            "telefone",
            ""
        ).strip()

        whatsapp = request.form.get(
            "whatsapp",
            ""
        ).strip()

        email_institucional = request.form.get(
            "email",
            ""
        ).strip().lower()

        site = request.form.get(
            "site",
            ""
        ).strip()

        diretor = request.form.get(
            "diretor",
            ""
        ).strip()

        coordenador1 = request.form.get(
            "coordenador1",
            ""
        ).strip()

        coordenador2 = request.form.get(
            "coordenador2",
            ""
        ).strip()

        coordenador3 = request.form.get(
            "coordenador3",
            ""
        ).strip()

        secretario = request.form.get(
            "secretario",
            ""
        ).strip()

        if not nome_instituicao:
            flash(
                "Informe o nome da instituição.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        # =====================================================
        # DADOS DO ADMINISTRADOR
        # =====================================================

        admin_nome = request.form.get(
            "admin_nome",
            ""
        ).strip()

        admin_email = request.form.get(
            "admin_email",
            ""
        ).strip().lower()

        admin_cpf = request.form.get(
            "admin_cpf",
            ""
        ).strip()

        admin_senha = request.form.get(
            "admin_senha",
            ""
        ).strip()

        admin_senha2 = request.form.get(
            "admin_senha2",
            ""
        ).strip()

        if not admin_nome:
            flash(
                "Informe o nome do administrador.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        if not admin_email:
            flash(
                "Informe o e-mail do administrador.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        if not admin_senha:
            flash(
                "Informe a senha do administrador.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        if admin_senha != admin_senha2:
            flash(
                "As senhas do administrador não conferem.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        if len(admin_senha) < 6:
            flash(
                "A senha do administrador deve possuir pelo menos 6 caracteres.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        # =====================================================
        # DADOS ACADÊMICOS
        # =====================================================

        tipo_instituicao = request.form.get(
            "tipo_instituicao",
            ""
        ).strip()

        ano_letivo = request.form.get(
            "ano_letivo",
            ""
        ).strip()

        modalidades = request.form.getlist(
            "modalidade_ensino"
        )

        etapas = request.form.getlist(
            "etapas_ensino"
        )

        componentes_recebidos = request.form.getlist(
            "componentes_curriculares"
        )

        if not tipo_instituicao:
            flash(
                "Selecione o tipo da instituição.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        if not ano_letivo:
            flash(
                "Selecione o ano letivo.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        if not etapas:
            flash(
                "Selecione pelo menos uma etapa de ensino.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        if not componentes_recebidos:
            flash(
                "Selecione pelo menos um componente curricular.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        modalidade_ensino = ", ".join(modalidades)
        etapas_ensino = ", ".join(etapas)

        # =====================================================
        # ORGANIZA OS COMPONENTES RECEBIDOS DO HTML
        # =====================================================

        componentes_processados = []
        componentes_repetidos = set()

        for componente_json in componentes_recebidos:

            try:
                componente = json.loads(componente_json)

                etapa = str(
                    componente.get("etapa", "")
                ).strip()

                nome = str(
                    componente.get("nome", "")
                ).strip()

                tipo = str(
                    componente.get("tipo", "padrao")
                ).strip().lower()

            except (
                json.JSONDecodeError,
                TypeError,
                AttributeError
            ):
                continue

            if not etapa or not nome:
                continue

            # Impede componente de uma etapa não selecionada
            if etapa not in etapas:
                continue

            # Aceita somente os dois tipos previstos
            if tipo not in ["padrao", "manual"]:
                tipo = "padrao"

            chave_componente = (
                etapa.lower(),
                nome.lower()
            )

            # Impede componentes repetidos
            if chave_componente in componentes_repetidos:
                continue

            componentes_repetidos.add(
                chave_componente
            )

            componentes_processados.append({
                "etapa": etapa,
                "nome": nome,
                "tipo": tipo
            })

        if not componentes_processados:
            flash(
                "Não foi possível identificar os componentes curriculares selecionados.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        # =====================================================
        # LOGO
        # =====================================================

        logo = request.files.get("logo")
        nome_logo = ""

        if logo and logo.filename:

            nome_logo = secure_filename(
                logo.filename
            )

            caminho_logo = os.path.join(
                app.config["UPLOAD_FOLDER"],
                nome_logo
            )

            logo.save(caminho_logo)

        # =====================================================
        # BANCO DE DADOS
        # =====================================================

        banco = conectar_banco()
        banco.row_factory = sqlite3.Row
        cursor = banco.cursor()

        try:

            # -------------------------------------------------
            # Verifica se o e-mail do administrador já existe
            # -------------------------------------------------

            cursor.execute("""
                SELECT id
                FROM usuarios
                WHERE LOWER(email) = LOWER(?)
                LIMIT 1
            """, (
                admin_email,
            ))

            usuario_existente = cursor.fetchone()

            if usuario_existente:

                flash(
                    "Já existe um usuário cadastrado com esse e-mail.",
                    "erro"
                )

                banco.close()

                return render_template(
                    "gestao/nova_instituicao.html"
                )

            # -------------------------------------------------
            # Busca o cargo Administrador da Instituição
            # -------------------------------------------------

            cursor.execute("""
                SELECT id
                FROM cargos
                WHERE nome = ?
                LIMIT 1
            """, (
                "Administrador da Instituição",
            ))

            cargo = cursor.fetchone()

            if cargo is None:

                cursor.execute("""
                    INSERT INTO cargos (nome)
                    VALUES (?)
                """, (
                    "Administrador da Instituição",
                ))

                cargo_id = cursor.lastrowid

            else:
                cargo_id = cargo["id"]

            # -------------------------------------------------
            # Cadastra a instituição
            # -------------------------------------------------

            cursor.execute("""
                INSERT INTO escolas (
                    nome_instituicao,
                    codigo_inep,
                    cnpj,
                    cep,
                    endereco,
                    cidade,
                    estado,
                    telefone,
                    whatsapp,
                    email,
                    site,
                    diretor,
                    coordenador1,
                    coordenador2,
                    coordenador3,
                    secretario,
                    tipo_instituicao,
                    ano_letivo,
                    modalidade_ensino,
                    etapas_ensino,
                    logo,
                    status,
                    criado_em
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                nome_instituicao,
                codigo_inep,
                cnpj,
                cep,
                endereco,
                cidade,
                estado,
                telefone,
                whatsapp,
                email_institucional,
                site,
                diretor,
                coordenador1,
                coordenador2,
                coordenador3,
                secretario,
                tipo_instituicao,
                ano_letivo,
                modalidade_ensino,
                etapas_ensino,
                nome_logo,
                1,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            ))

            escola_id = cursor.lastrowid

            # -------------------------------------------------
            # Cria e ativa o primeiro ano letivo oficial
            # -------------------------------------------------
            sincronizar_ano_letivo_instituicao(
                cursor,
                escola_id,
                ano_letivo,
                tornar_ativo=True
            )

            # -------------------------------------------------
            # Salva os componentes curriculares da instituição
            # -------------------------------------------------

            for componente in componentes_processados:

                cursor.execute("""
                    INSERT INTO componentes_curriculares (
                        escola_id,
                        etapa_ensino,
                        nome,
                        tipo,
                        ativo
                    )
                    VALUES (?, ?, ?, ?, 1)
                """, (
                    escola_id,
                    componente["etapa"],
                    componente["nome"],
                    componente["tipo"]
                ))

            # -------------------------------------------------
            # Cria o administrador da instituição
            # -------------------------------------------------

            cursor.execute("""
                INSERT INTO usuarios (
                    nome,
                    email,
                    senha,
                    cargo_id,
                    ativo,
                    escola_id,
                    cpf
                )
                VALUES (?, ?, ?, ?, 1, ?, ?)
            """, (
                admin_nome,
                admin_email,
                admin_senha,
                cargo_id,
                escola_id,
                admin_cpf
            ))

            banco.commit()

            flash(
                "Instituição, administrador e componentes curriculares cadastrados com sucesso!",
                "success"
            )

            return redirect(
                "/gestao/instituicoes"
            )

        except Exception as erro:

            banco.rollback()

            import traceback
            traceback.print_exc()

            print(
                "ERRO COMPLETO AO CADASTRAR INSTITUIÇÃO:",
                repr(erro)
            )

            flash(
                f"Erro ao cadastrar a instituição: {erro}",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        except Exception as erro:

            banco.rollback()

            import traceback
            traceback.print_exc()

            print(
                "ERRO COMPLETO AO CADASTRAR INSTITUIÇÃO:",
                repr(erro)
            )

            flash(
                f"Erro ao cadastrar a instituição: {erro}",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        finally:
            banco.close()

    return render_template(
        "gestao/nova_instituicao.html"
    )

@app.route("/usuarios")
def usuarios():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    usuario_cargo = session.get("usuario_cargo", "").strip()
    usuario_id = session.get("usuario_id")
    escola_id = session.get("escola_id")

    # Se for administrador da instituição e o escola_id
    # ainda não estiver na sessão, busca no banco.
    if (
        usuario_cargo == "Administrador da Instituição"
        and not escola_id
        and usuario_id
    ):
        cursor.execute("""
            SELECT escola_id
            FROM usuarios
            WHERE id = ?
            LIMIT 1
        """, (usuario_id,))

        usuario_logado = cursor.fetchone()

        if usuario_logado and usuario_logado["escola_id"]:
            escola_id = usuario_logado["escola_id"]
            session["escola_id"] = escola_id

    if usuario_cargo == "Administrador Geral":

        cursor.execute("""
            SELECT
                usuarios.id,
                usuarios.nome,
                usuarios.email,
                usuarios.cpf,
                usuarios.ativo,
                usuarios.escola_id,
                cargos.nome AS cargo,
                escolas.nome_instituicao
            FROM usuarios
            LEFT JOIN cargos
                ON usuarios.cargo_id = cargos.id
            LEFT JOIN escolas
                ON usuarios.escola_id = escolas.id
            ORDER BY usuarios.nome COLLATE NOCASE ASC
        """)

    elif usuario_cargo == "Administrador da Instituição":

        if not escola_id:
            banco.close()

            flash(
                "Seu usuário não está vinculado a uma instituição.",
                "erro"
            )

            return redirect("/")

        cursor.execute("""
            SELECT
                usuarios.id,
                usuarios.nome,
                usuarios.email,
                usuarios.cpf,
                usuarios.ativo,
                usuarios.escola_id,
                cargos.nome AS cargo,
                escolas.nome_instituicao
            FROM usuarios
            LEFT JOIN cargos
                ON usuarios.cargo_id = cargos.id
            LEFT JOIN escolas
                ON usuarios.escola_id = escolas.id
            WHERE usuarios.escola_id = ?
            ORDER BY usuarios.nome COLLATE NOCASE ASC
        """, (escola_id,))

    else:
        banco.close()
        return redirect("/")

    lista_usuarios = cursor.fetchall()

    banco.close()

    return render_template(
        "gestao/usuarios.html",
        usuarios=lista_usuarios
    )

@app.route("/gestao")
def gestao():

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT
            usuarios.id,
            usuarios.nome,
            usuarios.email,
            cargos.nome
        FROM usuarios
        JOIN cargos
        ON usuarios.cargo_id = cargos.id
        ORDER BY usuarios.nome
    """)

    usuarios = cursor.fetchall()

    banco.close()

    return render_template(
        "gestao.html",
        usuarios=usuarios
    )

@app.route("/cargos")
def cargos():

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT id, nome
        FROM cargos
        ORDER BY nome
    """)

    cargos = cursor.fetchall()

    banco.close()

    return render_template(
        "cargos.html",
        cargos=cargos
    )

import sqlite3
import traceback

from flask import (
    request,
    redirect,
    render_template,
    session,
    flash
)


def coluna_existe(cursor, tabela, coluna):
    """
    Verifica se determinada coluna existe em uma tabela.
    """
    cursor.execute(f"PRAGMA table_info({tabela})")
    colunas = cursor.fetchall()

    return any(
        registro[1] == coluna
        for registro in colunas
    )


def garantir_estrutura_usuarios(cursor):
    """
    Garante que as tabelas e colunas utilizadas no cadastro
    de usuários estejam disponíveis no banco.
    """

    # ==============================
    # COLUNA escola_id EM USUÁRIOS
    # ==============================

    if not coluna_existe(cursor, "usuarios", "escola_id"):
        cursor.execute("""
            ALTER TABLE usuarios
            ADD COLUMN escola_id INTEGER
        """)

    # ==============================
    # COLUNA cpf EM USUÁRIOS
    # ==============================

    if not coluna_existe(cursor, "usuarios", "cpf"):
        cursor.execute("""
            ALTER TABLE usuarios
            ADD COLUMN cpf TEXT
        """)

    # ==============================
    # COLUNA escola_id EM TURMAS
    # ==============================

    if not coluna_existe(cursor, "turmas", "escola_id"):
        cursor.execute("""
            ALTER TABLE turmas
            ADD COLUMN escola_id INTEGER
        """)

    # ==============================
    # PERMISSÕES DOS USUÁRIOS
    # ==============================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuario_permissoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            modulo TEXT NOT NULL,
            pode_acessar INTEGER DEFAULT 0,
            UNIQUE(usuario_id, modulo),
            FOREIGN KEY (usuario_id)
                REFERENCES usuarios(id)
                ON DELETE CASCADE
        )
    """)

    # ==============================
    # TURMAS DOS COORDENADORES
    # ==============================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coordenador_turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            turma_id INTEGER NOT NULL,
            UNIQUE(usuario_id, turma_id),
            FOREIGN KEY (usuario_id)
                REFERENCES usuarios(id)
                ON DELETE CASCADE,
            FOREIGN KEY (turma_id)
                REFERENCES turmas(id)
                ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS componentes_curriculares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            escola_id INTEGER NOT NULL,
            etapa_ensino TEXT NOT NULL,
            nome TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'padrao',
            ativo INTEGER NOT NULL DEFAULT 1,

            FOREIGN KEY (escola_id)
                REFERENCES escolas(id)
                ON DELETE CASCADE,

            UNIQUE (
                escola_id,
                etapa_ensino,
                nome
            )
        )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS professor_vinculos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        professor_id INTEGER NOT NULL,
        turma_id INTEGER NOT NULL,
        componente_id INTEGER NOT NULL,

        FOREIGN KEY (professor_id)
            REFERENCES usuarios(id)
            ON DELETE CASCADE,

        FOREIGN KEY (turma_id)
            REFERENCES turmas(id)
            ON DELETE CASCADE,

        FOREIGN KEY (componente_id)
            REFERENCES componentes_curriculares(id)
            ON DELETE CASCADE,

        UNIQUE (
            professor_id,
            turma_id,
            componente_id
        )
    )
""")

@app.route("/cadastrar_usuario", methods=["GET", "POST"])
def cadastrar_usuario():

    # ==============================
    # CONTROLE DE ACESSO
    # ==============================

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        flash(
            "Você não possui permissão para acessar esta página.",
            "erro"
        )
        return redirect("/login")

    banco = None

    try:
        banco = conectar_banco()
        banco.row_factory = sqlite3.Row

        cursor = banco.cursor()

        # Ativa o suporte a chaves estrangeiras
        cursor.execute("PRAGMA foreign_keys = ON")

        # Corrige automaticamente a estrutura do banco
        garantir_estrutura_usuarios(cursor)
        banco.commit()

        cargo_logado = session.get(
            "usuario_cargo",
            ""
        ).strip()

        escola_logada_id = session.get("escola_id")

        modulos_plataforma = [
            "Dashboard",
            "Instituições",
            "Usuários",
            "Turmas",
            "Alunos",
            "Questões",
            "Provas",
            "Relatórios"
        ]

        # ==============================
        # CARGOS DISPONÍVEIS
        # ==============================

        if cargo_logado == "Administrador Geral":

            cursor.execute("""
                SELECT
                    id,
                    nome
                FROM cargos
                ORDER BY nome
            """)

        else:

            cursor.execute("""
                SELECT
                    id,
                    nome
                FROM cargos
                WHERE nome != 'Administrador Geral'
                ORDER BY nome
            """)

        cargos = cursor.fetchall()

        # ==============================
        # INSTITUIÇÕES DISPONÍVEIS
        # ==============================

        if cargo_logado == "Administrador Geral":

            cursor.execute("""
                SELECT
                    id,
                    nome_instituicao
                FROM escolas
                WHERE COALESCE(status, 1) = 1
                ORDER BY nome_instituicao
            """)

            escolas = cursor.fetchall()

        else:
            escolas = []

        # ==============================
        # TURMAS DISPONÍVEIS
        # ==============================

        if cargo_logado == "Administrador Geral":

            cursor.execute("""
                SELECT
                    id,
                    nome,
                    ano,
                    turno,
                    escola_id
                FROM turmas
                ORDER BY
                    escola_id,
                    ano,
                    nome,
                    turno
            """)

        else:

            cursor.execute("""
                SELECT
                    id,
                    nome,
                    ano,
                    turno,
                    escola_id
                FROM turmas
                WHERE escola_id = ?
                ORDER BY
                    ano,
                    nome,
                    turno
            """, (
                escola_logada_id,
            ))

        turmas = cursor.fetchall()

        # ==============================
        # NOME DA INSTITUIÇÃO LOGADA
        # ==============================

        nome_instituicao = ""

        if (
            cargo_logado == "Administrador da Instituição"
            and escola_logada_id
        ):

            cursor.execute("""
                SELECT nome_instituicao
                FROM escolas
                WHERE id = ?
                LIMIT 1
            """, (
                escola_logada_id,
            ))

            escola = cursor.fetchone()

            if escola:
                nome_instituicao = escola["nome_instituicao"]

        # ==============================
        # SALVAMENTO DO USUÁRIO
        # ==============================

        if request.method == "POST":

            nome = request.form.get(
                "nome",
                ""
            ).strip()

            email = request.form.get(
                "email",
                ""
            ).strip().lower()

            cpf = request.form.get(
                "cpf",
                ""
            ).strip()

            senha = request.form.get(
                "senha",
                ""
            ).strip()

            confirmar_senha = request.form.get(
                "confirmar_senha",
                ""
            ).strip()

            cargo_id = request.form.get(
                "cargo_id",
                ""
            ).strip()

            modulos_selecionados = request.form.getlist(
                "modulos_permitidos"
            )

            turmas_vinculadas = request.form.getlist(
                "turmas_vinculadas"
            )

            # ==============================
            # VALIDAÇÕES BÁSICAS
            # ==============================

            if not nome:
                flash(
                    "Informe o nome do usuário.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            if not email:
                flash(
                    "Informe o e-mail do usuário.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            if "@" not in email:
                flash(
                    "Informe um endereço de e-mail válido.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            if not senha:
                flash(
                    "Informe uma senha.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            if len(senha) < 6:
                flash(
                    "A senha deve possuir pelo menos 6 caracteres.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            if senha != confirmar_senha:
                flash(
                    "As senhas não conferem.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            if not cargo_id:
                flash(
                    "Selecione um cargo.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")


                foto.stream.seek(0, os.SEEK_END)
                tamanho_foto = foto.stream.tell()
                foto.stream.seek(0)

                limite_foto = 5 * 1024 * 1024

                if tamanho_foto > limite_foto:
                    flash(
                        "A foto deve ter no máximo 5 MB.",
                        "erro"
                    )
                    return redirect("/cadastrar_usuario")

            # ==============================
            # VERIFICA O CARGO SELECIONADO
            # ==============================

            cursor.execute("""
                SELECT
                    id,
                    nome
                FROM cargos
                WHERE id = ?
                LIMIT 1
            """, (
                cargo_id,
            ))

            cargo_selecionado = cursor.fetchone()

            if cargo_selecionado is None:
                flash(
                    "O cargo selecionado é inválido.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            nome_cargo = cargo_selecionado["nome"].strip()

            # Administrador da Instituição não pode
            # criar Administrador Geral
            if (
                cargo_logado != "Administrador Geral"
                and nome_cargo == "Administrador Geral"
            ):
                flash(
                    "Você não pode criar um Administrador Geral.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            # ==============================
            # INSTITUIÇÃO DO NOVO USUÁRIO
            # ==============================

            if cargo_logado == "Administrador Geral":

                escola_id = request.form.get(
                    "escola_id",
                    ""
                ).strip()

                escola_id = escola_id or None

            else:
                escola_id = escola_logada_id

            # Administrador Geral não precisa ficar
            # vinculado a uma instituição
            if nome_cargo == "Administrador Geral":
                escola_id = None

            # Demais cargos precisam de instituição
            if (
                nome_cargo != "Administrador Geral"
                and not escola_id
            ):
                flash(
                    "Selecione a instituição do usuário.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            # Confirma se a instituição existe
            if escola_id:

                cursor.execute("""
                    SELECT id
                    FROM escolas
                    WHERE id = ?
                      AND COALESCE(status, 1) = 1
                    LIMIT 1
                """, (
                    escola_id,
                ))

                if cursor.fetchone() is None:
                    flash(
                        "A instituição selecionada é inválida ou está inativa.",
                        "erro"
                    )
                    return redirect("/cadastrar_usuario")

            # ==============================
            # CONTROLE DE PERMISSÕES
            # ==============================

            if cargo_logado == "Administrador da Instituição":

                modulos_selecionados = [
                    modulo
                    for modulo in modulos_selecionados
                    if modulo != "Instituições"
                ]

            # ==============================
            # VERIFICA E-MAIL DUPLICADO
            # ==============================

            cursor.execute("""
                SELECT id
                FROM usuarios
                WHERE LOWER(email) = LOWER(?)
                LIMIT 1
            """, (
                email,
            ))

            if cursor.fetchone():
                flash(
                    "Já existe um usuário cadastrado com este e-mail.",
                    "erro"
                )
                return redirect("/cadastrar_usuario")

            # ==============================
            # VERIFICA CPF DUPLICADO
            # ==============================

            if cpf:

                cursor.execute("""
                    SELECT id
                    FROM usuarios
                    WHERE REPLACE(
                        REPLACE(
                            REPLACE(cpf, '.', ''),
                            '-',
                            ''
                        ),
                        ' ',
                        ''
                    ) = REPLACE(
                        REPLACE(
                            REPLACE(?, '.', ''),
                            '-',
                            ''
                        ),
                        ' ',
                        ''
                    )
                    LIMIT 1
                """, (
                    cpf,
                ))

                if cursor.fetchone():
                    flash(
                        "Já existe um usuário cadastrado com este CPF.",
                        "erro"
                    )
                    return redirect("/cadastrar_usuario")

            # ==============================
            # INSERE O USUÁRIO
            # ==============================

            cursor.execute("""
                INSERT INTO usuarios (
                    nome,
                    email,
                    cpf,
                    senha,
                    cargo_id,
                    escola_id,
                    ativo
                )
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (
                nome,
                email,
                cpf or None,
                senha,
                cargo_id,
                escola_id
            ))

            usuario_id = cursor.lastrowid

            # ==============================
            # PERMISSÕES INDIVIDUAIS
            # ==============================

            for modulo in modulos_plataforma:

                pode_acessar = (
                    1
                    if modulo in modulos_selecionados
                    else 0
                )

                # Administrador Geral recebe acesso completo
                if nome_cargo == "Administrador Geral":
                    pode_acessar = 1

                # Usuários de instituição não acessam
                # o cadastro geral de instituições
                if (
                    cargo_logado == "Administrador da Instituição"
                    and modulo == "Instituições"
                ):
                    pode_acessar = 0

                cursor.execute("""
                    INSERT OR REPLACE INTO usuario_permissoes (
                        usuario_id,
                        modulo,
                        pode_acessar
                    )
                    VALUES (?, ?, ?)
                """, (
                    usuario_id,
                    modulo,
                    pode_acessar
                ))

            # ==============================
            # TURMAS DA COORDENAÇÃO
            # ==============================

            cargo_coordenacao = (
                "coordenador" in nome_cargo.lower()
                or "coordenação" in nome_cargo.lower()
                or "coordenacao" in nome_cargo.lower()
            )

            if cargo_coordenacao and escola_id:

                for turma_id in turmas_vinculadas:

                    cursor.execute("""
                        SELECT id
                        FROM turmas
                        WHERE id = ?
                          AND escola_id = ?
                        LIMIT 1
                    """, (
                        turma_id,
                        escola_id
                    ))

                    turma_valida = cursor.fetchone()

                    if turma_valida:

                        cursor.execute("""
                            INSERT OR IGNORE INTO coordenador_turmas (
                                usuario_id,
                                turma_id
                            )
                            VALUES (?, ?)
                        """, (
                            usuario_id,
                            turma_id
                        ))

            banco.commit()

            flash(
                "Usuário cadastrado com sucesso.",
                "success"
            )

            return redirect("/usuarios")

        # ==============================
        # ABERTURA DO FORMULÁRIO
        # ==============================

        return render_template(
            "gestao/cadastrar_usuario.html",
            cargos=cargos,
            escolas=escolas,
            turmas=turmas,
            nome_instituicao=nome_instituicao,
            modulos_plataforma=modulos_plataforma
        )

    except sqlite3.IntegrityError as erro:

        if banco:
            banco.rollback()

        traceback.print_exc()

        print(
            f"Erro de integridade ao cadastrar usuário: {erro}"
        )

        flash(
            "Não foi possível cadastrar o usuário. "
            "Verifique se o e-mail ou CPF já está cadastrado.",
            "erro"
        )

        return redirect("/cadastrar_usuario")

    except sqlite3.OperationalError as erro:

        if banco:
            banco.rollback()

        traceback.print_exc()

        print(
            f"Erro na estrutura do banco de dados: {erro}"
        )

        flash(
            f"Erro na estrutura do banco de dados: {erro}",
            "erro"
        )

        return redirect("/usuarios")

    except Exception as erro:

        if banco:
            banco.rollback()

        traceback.print_exc()

        print(
            f"Erro ao cadastrar usuário: {erro}"
        )

        flash(
            "Ocorreu um erro ao cadastrar o usuário.",
            "erro"
        )

        return redirect("/usuarios")

    finally:

        if banco:
            banco.close()

@app.route("/editar_usuario/<int:id>", methods=["GET", "POST"])
def editar_usuario(id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        flash(
            "Você não possui permissão para editar usuários.",
            "erro"
        )
        return redirect("/usuarios")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo_logado = session.get(
        "usuario_cargo",
        ""
    ).strip()

    escola_logada_id = session.get("escola_id")

    modulos_plataforma = [
        "Dashboard",
        "Instituições",
        "Usuários",
        "Turmas",
        "Professores",
        "Alunos",
        "Questões",
        "Provas",
        "Relatórios"
    ]

    try:

        # ==========================================
        # BUSCA O USUÁRIO COM SEGURANÇA
        # ==========================================

        if cargo_logado == "Administrador Geral":

            cursor.execute("""
                SELECT
                    usuarios.*,
                    escolas.nome_instituicao
                FROM usuarios
                LEFT JOIN escolas
                    ON usuarios.escola_id = escolas.id
                WHERE usuarios.id = ?
                LIMIT 1
            """, (
                id,
            ))

        else:

            cursor.execute("""
                SELECT
                    usuarios.*,
                    escolas.nome_instituicao
                FROM usuarios
                LEFT JOIN escolas
                    ON usuarios.escola_id = escolas.id
                WHERE usuarios.id = ?
                  AND usuarios.escola_id = ?
                LIMIT 1
            """, (
                id,
                escola_logada_id
            ))

        usuario = cursor.fetchone()

        if usuario is None:

            flash(
                "Usuário não encontrado ou sem permissão de acesso.",
                "erro"
            )

            return redirect("/usuarios")

        # ==========================================
        # CARGOS DISPONÍVEIS
        # ==========================================

        if cargo_logado == "Administrador Geral":

            cursor.execute("""
                SELECT
                    id,
                    nome
                FROM cargos
                ORDER BY nome
            """)

        else:

            cursor.execute("""
                SELECT
                    id,
                    nome
                FROM cargos
                WHERE nome != 'Administrador Geral'
                ORDER BY nome
            """)

        cargos = cursor.fetchall()

        # ==========================================
        # INSTITUIÇÕES DISPONÍVEIS
        # ==========================================

        if cargo_logado == "Administrador Geral":

            cursor.execute("""
                SELECT
                    id,
                    nome_instituicao
                FROM escolas
                WHERE COALESCE(status, 1) = 1
                ORDER BY nome_instituicao
            """)

            escolas = cursor.fetchall()

        else:
            escolas = []

        # ==========================================
        # SALVAMENTO
        # ==========================================

        if request.method == "POST":

            nome = request.form.get(
                "nome",
                ""
            ).strip()

            email = request.form.get(
                "email",
                ""
            ).strip().lower()

            cpf = request.form.get(
                "cpf",
                ""
            ).strip()

            nova_senha = request.form.get(
                "senha",
                ""
            ).strip()

            cargo_id = request.form.get(
                "cargo_id",
                ""
            ).strip()

            modulos_selecionados = request.form.getlist(
                "modulos_permitidos"
            )

            turmas_selecionadas = request.form.getlist(
                "turmas_vinculadas"
            )

            # ======================================
            # VALIDAÇÕES
            # ======================================

            if not nome:
                flash(
                    "Informe o nome do usuário.",
                    "erro"
                )
                return redirect(f"/editar_usuario/{id}")

            if not email:
                flash(
                    "Informe o e-mail do usuário.",
                    "erro"
                )
                return redirect(f"/editar_usuario/{id}")

            if "@" not in email:
                flash(
                    "Informe um endereço de e-mail válido.",
                    "erro"
                )
                return redirect(f"/editar_usuario/{id}")

            if not cargo_id:
                flash(
                    "Selecione um cargo.",
                    "erro"
                )
                return redirect(f"/editar_usuario/{id}")

            if nova_senha and len(nova_senha) < 6:
                flash(
                    "A nova senha deve possuir pelo menos 6 caracteres.",
                    "erro"
                )
                return redirect(f"/editar_usuario/{id}")

            # ======================================
            # VERIFICA E-MAIL DUPLICADO
            # ======================================

            cursor.execute("""
                SELECT id
                FROM usuarios
                WHERE LOWER(email) = LOWER(?)
                  AND id != ?
                LIMIT 1
            """, (
                email,
                id
            ))

            if cursor.fetchone():

                flash(
                    "Este e-mail já está sendo utilizado por outro usuário.",
                    "erro"
                )

                return redirect(f"/editar_usuario/{id}")

            # ======================================
            # VERIFICA O CARGO
            # ======================================

            cursor.execute("""
                SELECT
                    id,
                    nome
                FROM cargos
                WHERE id = ?
                LIMIT 1
            """, (
                cargo_id,
            ))

            cargo_selecionado = cursor.fetchone()

            if cargo_selecionado is None:

                flash(
                    "O cargo selecionado não existe.",
                    "erro"
                )

                return redirect(f"/editar_usuario/{id}")

            nome_cargo = cargo_selecionado["nome"].strip()

            if (
                cargo_logado != "Administrador Geral"
                and nome_cargo == "Administrador Geral"
            ):

                flash(
                    "Você não pode atribuir o cargo de Administrador Geral.",
                    "erro"
                )

                return redirect(f"/editar_usuario/{id}")

            # ======================================
            # DEFINE A INSTITUIÇÃO
            # ======================================

            if cargo_logado == "Administrador Geral":

                escola_id = request.form.get(
                    "escola_id",
                    ""
                ).strip()

                escola_id = escola_id or None

            else:
                escola_id = escola_logada_id

            # Administrador Geral pode não possuir instituição
            if nome_cargo == "Administrador Geral":
                escola_id = None

            # Outros cargos precisam estar vinculados
            if (
                nome_cargo != "Administrador Geral"
                and not escola_id
            ):

                flash(
                    "Selecione a instituição do usuário.",
                    "erro"
                )

                return redirect(f"/editar_usuario/{id}")

            # Confirma se a instituição existe e está ativa
            if escola_id:

                cursor.execute("""
                    SELECT id
                    FROM escolas
                    WHERE id = ?
                      AND COALESCE(status, 1) = 1
                    LIMIT 1
                """, (
                    escola_id,
                ))

                if cursor.fetchone() is None:

                    flash(
                        "A instituição selecionada é inválida ou está inativa.",
                        "erro"
                    )

                    return redirect(f"/editar_usuario/{id}")

            # Administrador de instituição não pode
            # liberar acesso ao módulo Instituições
            if cargo_logado == "Administrador da Instituição":

                modulos_selecionados = [
                    modulo
                    for modulo in modulos_selecionados
                    if modulo != "Instituições"
                ]

            # ======================================
            # ATUALIZA O USUÁRIO
            # ======================================

            if nova_senha:

                cursor.execute("""
                    UPDATE usuarios
                    SET
                        nome = ?,
                        email = ?,
                        cpf = ?,
                        senha = ?,
                        cargo_id = ?,
                        escola_id = ?
                    WHERE id = ?
                """, (
                    nome,
                    email,
                    cpf or None,
                    nova_senha,
                    cargo_id,
                    escola_id,
                    id
                ))

            else:

                cursor.execute("""
                    UPDATE usuarios
                    SET
                        nome = ?,
                        email = ?,
                        cpf = ?,
                        cargo_id = ?,
                        escola_id = ?
                    WHERE id = ?
                """, (
                    nome,
                    email,
                    cpf or None,
                    cargo_id,
                    escola_id,
                    id
                ))

            # ======================================
            # ATUALIZA AS PERMISSÕES
            # ======================================

            cursor.execute("""
                DELETE FROM usuario_permissoes
                WHERE usuario_id = ?
            """, (
                id,
            ))

            for modulo in modulos_plataforma:

                pode_acessar = (
                    1
                    if modulo in modulos_selecionados
                    else 0
                )

                # Administrador Geral recebe acesso completo
                if nome_cargo == "Administrador Geral":
                    pode_acessar = 1

                # Usuários administrados pela instituição
                # nunca recebem o módulo Instituições
                if (
                    cargo_logado == "Administrador da Instituição"
                    and modulo == "Instituições"
                ):
                    pode_acessar = 0

                cursor.execute("""
                    INSERT INTO usuario_permissoes (
                        usuario_id,
                        modulo,
                        pode_acessar
                    )
                    VALUES (?, ?, ?)
                """, (
                    id,
                    modulo,
                    pode_acessar
                ))

            # ======================================
            # ATUALIZA AS TURMAS DA COORDENAÇÃO
            # ======================================

            cursor.execute("""
                DELETE FROM coordenador_turmas
                WHERE usuario_id = ?
            """, (
                id,
            ))

            nome_cargo_minusculo = nome_cargo.lower()

            cargo_coordenacao = (
                "coordenador" in nome_cargo_minusculo
                or "coordenação" in nome_cargo_minusculo
                or "coordenacao" in nome_cargo_minusculo
            )

            if cargo_coordenacao and escola_id:

                for turma_id in turmas_selecionadas:

                    cursor.execute("""
                        SELECT id
                        FROM turmas
                        WHERE id = ?
                          AND escola_id = ?
                        LIMIT 1
                    """, (
                        turma_id,
                        escola_id
                    ))

                    turma_valida = cursor.fetchone()

                    if turma_valida:

                        cursor.execute("""
                            INSERT OR IGNORE INTO coordenador_turmas (
                                usuario_id,
                                turma_id
                            )
                            VALUES (?, ?)
                        """, (
                            id,
                            turma_id
                        ))

            banco.commit()

            flash(
                "Usuário atualizado com sucesso.",
                "success"
            )

            return redirect("/usuarios")

        # ==========================================
        # PERMISSÕES JÁ MARCADAS
        # ==========================================

        cursor.execute("""
            SELECT modulo
            FROM usuario_permissoes
            WHERE usuario_id = ?
              AND pode_acessar = 1
        """, (
            id,
        ))

        permissoes_marcadas = [
            linha["modulo"]
            for linha in cursor.fetchall()
        ]

        # ==========================================
        # TURMAS DISPONÍVEIS
        # ==========================================

        if cargo_logado == "Administrador Geral":

            cursor.execute("""
                SELECT
                    id,
                    nome,
                    ano,
                    turno,
                    escola_id
                FROM turmas
                ORDER BY escola_id, ano, nome, turno
            """)

        else:

            cursor.execute("""
                SELECT
                    id,
                    nome,
                    ano,
                    turno,
                    escola_id
                FROM turmas
                WHERE escola_id = ?
                ORDER BY ano, nome, turno
            """, (
                escola_logada_id,
            ))

        turmas = cursor.fetchall()

        # ==========================================
        # TURMAS JÁ MARCADAS
        # ==========================================

        cursor.execute("""
            SELECT turma_id
            FROM coordenador_turmas
            WHERE usuario_id = ?
        """, (
            id,
        ))

        turmas_marcadas = [
            linha["turma_id"]
            for linha in cursor.fetchall()
        ]

        # ==========================================
        # ABRE A TELA
        # ==========================================

        return render_template(
            "gestao/editar_usuario.html",
            usuario=usuario,
            cargos=cargos,
            escolas=escolas,
            turmas=turmas,
            turmas_marcadas=turmas_marcadas,
            modulos_plataforma=modulos_plataforma,
            permissoes_marcadas=permissoes_marcadas
        )

    except sqlite3.IntegrityError as erro:

        banco.rollback()

        print(
            f"Erro de integridade ao editar usuário: {erro}"
        )

        flash(
            "Não foi possível salvar. Verifique se o e-mail ou CPF já está cadastrado.",
            "erro"
        )

        return redirect(f"/editar_usuario/{id}")

    except Exception as erro:

        banco.rollback()

        import traceback
        traceback.print_exc()

        print(
            f"Erro ao editar usuário: {erro}"
        )

        flash(
            "Ocorreu um erro ao editar o usuário.",
            "erro"
        )

        return redirect("/usuarios")

    finally:
        banco.close()

@app.route("/ativar_inativar_usuario/<int:id>")
def ativar_inativar_usuario(id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/login")

    # Impede o usuário conectado de inativar a própria conta
    if id == session.get("usuario_id"):
        flash(
            "Você não pode inativar o usuário que está conectado.",
            "erro"
        )
        return redirect("/usuarios")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo_logado = session.get("usuario_cargo")
    escola_id = session.get("escola_id")

    # Administrador Geral pode alterar qualquer usuário.
    # Administrador da Instituição altera apenas usuários da própria escola.
    if cargo_logado == "Administrador Geral":

        cursor.execute("""
            SELECT id, ativo
            FROM usuarios
            WHERE id = ?
            LIMIT 1
        """, (id,))

    else:

        cursor.execute("""
            SELECT id, ativo
            FROM usuarios
            WHERE id = ?
              AND escola_id = ?
            LIMIT 1
        """, (
            id,
            escola_id
        ))

    usuario = cursor.fetchone()

    if usuario is None:
        banco.close()

        flash(
            "Usuário não encontrado ou sem permissão para esta ação.",
            "erro"
        )

        return redirect("/usuarios")

    novo_status = 0 if usuario["ativo"] == 1 else 1

    cursor.execute("""
        UPDATE usuarios
        SET ativo = ?
        WHERE id = ?
    """, (
        novo_status,
        id
    ))

    banco.commit()
    banco.close()

    if novo_status == 1:
        flash("Usuário ativado com sucesso.", "success")
    else:
        flash("Usuário inativado com sucesso.", "success")

    return redirect("/usuarios")

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "GET" and session.get("usuario_id"):
        return redirect("/")

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        senha = request.form.get("senha", "").strip()

        if not email or not senha:
            flash("Informe o usuário/e-mail e a senha.", "erro")
            return render_template("login.html")

        banco = conectar_banco()
        banco.row_factory = sqlite3.Row
        cursor = banco.cursor()

        try:
            cursor.execute("""
                SELECT
                    usuarios.*,
                    cargos.nome AS cargo
                FROM usuarios
                LEFT JOIN cargos
                    ON usuarios.cargo_id = cargos.id
                WHERE LOWER(TRIM(usuarios.email)) = LOWER(TRIM(?))
                  AND usuarios.ativo = 1
                LIMIT 1
            """, (email,))

            usuario = cursor.fetchone()

            senha_valida = False

            if usuario:
                senha_salva = usuario["senha"] or ""

                try:
                    senha_valida = check_password_hash(senha_salva, senha)
                except (ValueError, TypeError):
                    senha_valida = False

                # Compatibilidade temporária com senhas antigas em texto puro.
                if not senha_valida and senha_salva == senha:
                    senha_valida = True
                    cursor.execute(
                        "UPDATE usuarios SET senha = ? WHERE id = ?",
                        (generate_password_hash(senha), usuario["id"])
                    )
                    banco.commit()

            if not usuario or not senha_valida:
                flash("Usuário, senha ou status inválido.", "erro")
                return render_template("login.html")

            session.clear()
            session["usuario_id"] = usuario["id"]
            session["usuario_nome"] = usuario["nome"]
            session["usuario_cargo"] = usuario["cargo"] or ""
            session["escola_id"] = usuario["escola_id"]

            if usuario["escola_id"]:
                atualizar_ano_letivo_na_sessao(usuario["escola_id"])

            if usuario["escola_id"]:
                session["escola_id"] = int(usuario["escola_id"])
                garantir_ano_atual_para_escola(usuario["escola_id"])
                atualizar_ano_letivo_na_sessao(usuario["escola_id"])
            else:
                obter_ano_global_administrador()

            return redirect("/")

        except sqlite3.Error as erro:
            print("ERRO NO LOGIN:", erro)
            flash("Não foi possível realizar o login.", "erro")
            return render_template("login.html")

        finally:
            banco.close()

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/permissoes/<int:cargo_id>")
def permissoes(cargo_id):

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    modulos = [
        "Dashboard",
        "Turmas",
        "Alunos",
        "Professores",
        "Questões",
        "Provas",
        "Correção",
        "Relatórios",
        "Gestão"
    ]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("SELECT nome FROM cargos WHERE id = ?", (cargo_id,))
    cargo = cursor.fetchone()

    cursor.execute("""
        SELECT modulo
        FROM permissoes
        WHERE cargo_id = ?
        AND pode_acessar = 1
    """, (cargo_id,))

    permissoes_salvas = [linha[0] for linha in cursor.fetchall()]

    banco.close()

    return render_template(
        "permissoes.html",
        cargo=cargo,
        cargo_id=cargo_id,
        modulos=modulos,
        permissoes_salvas=permissoes_salvas
    )

def permissao_modulo(modulo):

    if "usuario_id" not in session:
        return False

    usuario_id = session.get("usuario_id")
    cargo = session.get("usuario_cargo", "").strip()

    # Administrador Geral possui acesso completo
    if cargo == "Administrador Geral":
        return True

    # Administrador da Instituição possui acesso completo,
    # exceto ao gerenciamento geral das instituições
    if cargo == "Administrador da Instituição":
        return modulo != "Instituições"

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        # Verifica se o usuário possui permissões individuais cadastradas
        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM usuario_permissoes
            WHERE usuario_id = ?
        """, (usuario_id,))

        possui_permissoes_individuais = (
            cursor.fetchone()["total"] > 0
        )

        # Se existem permissões individuais, elas são definitivas:
        # marcado = acessa; desmarcado = não acessa.
        if possui_permissoes_individuais:

            cursor.execute("""
                SELECT pode_acessar
                FROM usuario_permissoes
                WHERE usuario_id = ?
                  AND modulo = ?
                LIMIT 1
            """, (
                usuario_id,
                modulo
            ))

            permissao_individual = cursor.fetchone()

            if permissao_individual is None:
                return False

            return permissao_individual["pode_acessar"] == 1

        # Só usa as permissões do cargo quando o usuário ainda
        # não possui nenhuma configuração individual.
        cursor.execute("""
            SELECT cargo_id
            FROM usuarios
            WHERE id = ?
            LIMIT 1
        """, (usuario_id,))

        usuario = cursor.fetchone()

        if usuario is None:
            return False

        cursor.execute("""
            SELECT pode_acessar
            FROM permissoes
            WHERE cargo_id = ?
              AND modulo = ?
            LIMIT 1
        """, (
            usuario["cargo_id"],
            modulo
        ))

        permissao_cargo = cursor.fetchone()

        if permissao_cargo is None:
            return False

        return permissao_cargo["pode_acessar"] == 1

    finally:
        banco.close()

@app.context_processor
def inject_permissoes():
    return dict(
        permissao_modulo=permissao_modulo
    )

@app.route("/salvar_permissoes/<int:cargo_id>", methods=["POST"])
def salvar_permissoes(cargo_id):

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    modulos_marcados = request.form.getlist("modulos")

    todos_modulos = [
        "Dashboard",
        "Turmas",
        "Alunos",
        "Professores",
        "Questões",
        "Provas",
        "Correção",
        "Relatórios",
        "Gestão"
    ]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "DELETE FROM permissoes WHERE cargo_id = ?",
        (cargo_id,)
    )

    for modulo in todos_modulos:
        pode_acessar = 1 if modulo in modulos_marcados else 0

        cursor.execute("""
            INSERT INTO permissoes
            (cargo_id, modulo, pode_acessar)
            VALUES (?, ?, ?)
        """, (
            cargo_id,
            modulo,
            pode_acessar
        ))

    banco.commit()

    cursor.execute(
        "SELECT nome FROM cargos WHERE id = ?",
        (cargo_id,)
    )

    nome_cargo = cursor.fetchone()[0]

    flash(
        f"Permissões do cargo '{nome_cargo}' salvas com sucesso!",
        "success"
    )

    banco.close()

    return redirect(f"/permissoes/{cargo_id}")

@app.route("/acesso_negado")
def acesso_negado():
    return render_template("acesso_negado.html")

@app.route("/recuperar_senha", methods=["POST"])
def recuperar_senha():
    email = request.form["email"].strip()

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT id, nome, email
        FROM usuarios
        WHERE email = ?
        AND ativo = 1
    """, (email,))

    usuario = cursor.fetchone()

    if usuario:
        import random

        usuario_id = usuario[0]
        nome_usuario = usuario[1]
        email_usuario = usuario[2]

        codigo = str(random.randint(100000, 999999))
        criado_em = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT INTO codigos_recuperacao
            (usuario_id, codigo, usado, criado_em)
            VALUES (?, ?, 0, ?)
        """, (usuario_id, codigo, criado_em))

        banco.commit()

        msg = Message(
            subject="Código de recuperação de senha - Plataforma de Avaliação",
            recipients=[email_usuario]
        )

        msg.html = f"""
        <h2>Recuperação de senha</h2>

        <p>Olá, {nome_usuario}!</p>

        <p>Recebemos uma solicitação para redefinir sua senha.</p>

        <p>Use o código abaixo para criar uma nova senha:</p>

        <h1 style="color:#1e3a8a; letter-spacing:4px;">
            {codigo}
        </h1>

        <p>Este código expira em 30 minutos.</p>

        <p>Se você não solicitou esta alteração, ignore este e-mail.</p>
        """

        mail.send(msg)

    banco.close()

    return render_template("verificar_codigo.html", email=email)

@app.route("/verificar_codigo", methods=["POST"])
def verificar_codigo():

    email = request.form["email"].strip()
    codigo = request.form["codigo"].strip()

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT usuarios.id
        FROM usuarios
        INNER JOIN codigos_recuperacao
            ON usuarios.id = codigos_recuperacao.usuario_id
        WHERE usuarios.email = ?
            AND codigos_recuperacao.codigo = ?
            AND codigos_recuperacao.usado = 0
        ORDER BY codigos_recuperacao.id DESC
        LIMIT 1
    """, (email, codigo))

    registro = cursor.fetchone()

    banco.close()

    if not registro:
        return "Código inválido ou já utilizado."

    return render_template(
        "nova_senha.html",
        email=email,
        codigo=codigo
    )

@app.route("/salvar_senha_usuario", methods=["POST"])
def salvar_senha_usuario():

    email = request.form["email"].strip()
    codigo = request.form["codigo"].strip()
    nova_senha = request.form["nova_senha"].strip()
    confirmar_senha = request.form["confirmar_senha"].strip()

    if nova_senha != confirmar_senha:
        return "As senhas não conferem."

    if len(nova_senha) < 4:
        return "A senha precisa ter pelo menos 4 caracteres."

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT usuarios.id
        FROM usuarios
        INNER JOIN codigos_recuperacao
            ON usuarios.id = codigos_recuperacao.usuario_id
        WHERE usuarios.email = ?
            AND codigos_recuperacao.codigo = ?
            AND codigos_recuperacao.usado = 0
        ORDER BY codigos_recuperacao.id DESC
        LIMIT 1
    """, (email, codigo))

    usuario = cursor.fetchone()

    if not usuario:
        banco.close()
        return "Código inválido ou expirado."

    usuario_id = usuario[0]

    cursor.execute("""
        UPDATE usuarios
        SET senha = ?
        WHERE id = ?
    """, (nova_senha, usuario_id))

    cursor.execute("""
        UPDATE codigos_recuperacao
        SET usado = 1
        WHERE usuario_id = ?
        AND codigo = ?
    """, (usuario_id, codigo))

    banco.commit()
    banco.close()

    return redirect("/login")

criar_tabelas()
sincronizar_anos_letivos_legados()

@app.route("/gestao/instituicoes/editar/<int:id>", methods=["GET", "POST"])
def editar_instituicao(id):

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cursor.execute("""
            SELECT *
            FROM escolas
            WHERE id = ?
            LIMIT 1
        """, (id,))

        escola = cursor.fetchone()

        if escola is None:
            flash("Instituição não encontrada.", "erro")
            return redirect("/gestao/instituicoes")

        cursor.execute("""
            SELECT usuarios.*
            FROM usuarios
            LEFT JOIN cargos
                ON cargos.id = usuarios.cargo_id
            WHERE usuarios.escola_id = ?
              AND cargos.nome = 'Administrador da Instituição'
            ORDER BY usuarios.id
            LIMIT 1
        """, (id,))

        administrador = cursor.fetchone()

        if request.method == "POST":

            nome_instituicao = request.form.get("nome_instituicao", "").strip()
            codigo_inep = request.form.get("codigo_inep", "").strip()
            cnpj = request.form.get("cnpj", "").strip()
            cep = request.form.get("cep", "").strip()
            endereco = request.form.get("endereco", "").strip()
            cidade = request.form.get("cidade", "").strip()
            estado = request.form.get("estado", "").strip()
            telefone = request.form.get("telefone", "").strip()
            whatsapp = request.form.get("whatsapp", "").strip()
            email_institucional = request.form.get("email", "").strip().lower()
            site = request.form.get("site", "").strip()
            diretor = request.form.get("diretor", "").strip()
            coordenador1 = request.form.get("coordenador1", "").strip()
            coordenador2 = request.form.get("coordenador2", "").strip()
            coordenador3 = request.form.get("coordenador3", "").strip()
            secretario = request.form.get("secretario", "").strip()
            tipo_instituicao = request.form.get("tipo_instituicao", "").strip()
            ano_letivo = request.form.get("ano_letivo", "").strip()

            modalidades = request.form.getlist("modalidade_ensino")
            etapas = request.form.getlist("etapas_ensino")
            componentes_recebidos = request.form.getlist("componentes_curriculares")

            admin_nome = request.form.get("admin_nome", "").strip()
            admin_email = request.form.get("admin_email", "").strip().lower()
            admin_cpf = request.form.get("admin_cpf", "").strip()
            admin_senha = request.form.get("admin_senha", "").strip()

            if not nome_instituicao:
                flash("Informe o nome da instituição.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if not tipo_instituicao:
                flash("Selecione o tipo da instituição.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if not ano_letivo:
                flash("Selecione o ano letivo.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if not etapas:
                flash("Selecione pelo menos uma etapa de ensino.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if not componentes_recebidos:
                flash("Selecione pelo menos um componente curricular.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if not admin_nome:
                flash("Informe o nome do administrador.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if not admin_email:
                flash("Informe o e-mail do administrador.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if admin_senha and len(admin_senha) < 6:
                flash("A nova senha deve possuir pelo menos 6 caracteres.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if not administrador and not admin_senha:
                flash("Informe uma senha para criar o administrador da instituição.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            if administrador:
                cursor.execute("""
                    SELECT id
                    FROM usuarios
                    WHERE LOWER(email) = LOWER(?)
                      AND id != ?
                    LIMIT 1
                """, (admin_email, administrador["id"]))
            else:
                cursor.execute("""
                    SELECT id
                    FROM usuarios
                    WHERE LOWER(email) = LOWER(?)
                    LIMIT 1
                """, (admin_email,))

            if cursor.fetchone():
                flash("Este e-mail já está sendo utilizado por outro usuário.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            componentes_processados = []
            componentes_repetidos = set()

            for componente_json in componentes_recebidos:
                try:
                    componente = json.loads(componente_json)
                    etapa = str(componente.get("etapa", "")).strip()
                    nome = str(componente.get("nome", "")).strip()
                    tipo = str(componente.get("tipo", "padrao")).strip().lower()
                except (json.JSONDecodeError, TypeError, AttributeError):
                    continue

                if not etapa or not nome:
                    continue

                if etapa not in etapas:
                    continue

                if tipo not in ["padrao", "manual"]:
                    tipo = "padrao"

                chave = (etapa.lower(), nome.lower())

                if chave in componentes_repetidos:
                    continue

                componentes_repetidos.add(chave)
                componentes_processados.append({
                    "etapa": etapa,
                    "nome": nome,
                    "tipo": tipo
                })

            if not componentes_processados:
                flash("Não foi possível identificar os componentes curriculares selecionados.", "erro")
                return redirect(f"/gestao/instituicoes/editar/{id}")

            modalidade_ensino = ", ".join(modalidades)
            etapas_ensino = ", ".join(etapas)

            logo = request.files.get("logo")
            nome_logo = escola["logo"] or ""

            if logo and logo.filename:
                nome_logo = secure_filename(logo.filename)
                logo.save(os.path.join(app.config["UPLOAD_FOLDER"], nome_logo))

            cursor.execute("""
                UPDATE escolas
                SET
                    nome_instituicao = ?,
                    codigo_inep = ?,
                    cnpj = ?,
                    cep = ?,
                    endereco = ?,
                    cidade = ?,
                    estado = ?,
                    telefone = ?,
                    whatsapp = ?,
                    email = ?,
                    site = ?,
                    diretor = ?,
                    coordenador1 = ?,
                    coordenador2 = ?,
                    coordenador3 = ?,
                    secretario = ?,
                    tipo_instituicao = ?,
                    ano_letivo = ?,
                    modalidade_ensino = ?,
                    etapas_ensino = ?,
                    logo = ?
                WHERE id = ?
            """, (
                nome_instituicao,
                codigo_inep,
                cnpj,
                cep,
                endereco,
                cidade,
                estado,
                telefone,
                whatsapp,
                email_institucional,
                site,
                diretor,
                coordenador1,
                coordenador2,
                coordenador3,
                secretario,
                tipo_instituicao,
                ano_letivo,
                modalidade_ensino,
                etapas_ensino,
                nome_logo,
                id
            ))

            # Mantém a tabela oficial de anos letivos sincronizada.
            # O ano selecionado na edição passa a ser o ano ativo da escola.
            sincronizar_ano_letivo_instituicao(
                cursor,
                id,
                ano_letivo,
                tornar_ativo=True
            )

            cursor.execute("""
                DELETE FROM componentes_curriculares
                WHERE escola_id = ?
            """, (id,))

            for componente in componentes_processados:
                cursor.execute("""
                    INSERT INTO componentes_curriculares (
                        escola_id,
                        etapa_ensino,
                        nome,
                        tipo,
                        ativo
                    )
                    VALUES (?, ?, ?, ?, 1)
                """, (
                    id,
                    componente["etapa"],
                    componente["nome"],
                    componente["tipo"]
                ))

            if administrador:
                if admin_senha:
                    cursor.execute("""
                        UPDATE usuarios
                        SET nome = ?, email = ?, cpf = ?, senha = ?, escola_id = ?, ativo = 1
                        WHERE id = ?
                    """, (
                        admin_nome,
                        admin_email,
                        admin_cpf,
                        admin_senha,
                        id,
                        administrador["id"]
                    ))
                else:
                    cursor.execute("""
                        UPDATE usuarios
                        SET nome = ?, email = ?, cpf = ?, escola_id = ?, ativo = 1
                        WHERE id = ?
                    """, (
                        admin_nome,
                        admin_email,
                        admin_cpf,
                        id,
                        administrador["id"]
                    ))
            else:
                cursor.execute("""
                    SELECT id
                    FROM cargos
                    WHERE nome = ?
                    LIMIT 1
                """, ("Administrador da Instituição",))

                cargo = cursor.fetchone()

                if cargo is None:
                    cursor.execute("""
                        INSERT INTO cargos (nome)
                        VALUES (?)
                    """, ("Administrador da Instituição",))
                    cargo_id = cursor.lastrowid
                else:
                    cargo_id = cargo["id"]

                cursor.execute("""
                    INSERT INTO usuarios (
                        nome,
                        email,
                        senha,
                        cargo_id,
                        ativo,
                        escola_id,
                        cpf
                    )
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                """, (
                    admin_nome,
                    admin_email,
                    admin_senha,
                    cargo_id,
                    id,
                    admin_cpf
                ))

            banco.commit()

            flash(
                "Instituição, administrador e componentes curriculares atualizados com sucesso!",
                "success"
            )

            return redirect("/gestao/instituicoes")

        modalidades_marcadas = [
            item.strip()
            for item in (escola["modalidade_ensino"] or "").split(",")
            if item.strip()
        ]

        etapas_marcadas = [
            item.strip()
            for item in (escola["etapas_ensino"] or "").split(",")
            if item.strip()
        ]

        cursor.execute("""
            SELECT
                id,
                escola_id,
                etapa_ensino,
                nome,
                tipo,
                ativo
            FROM componentes_curriculares
            WHERE escola_id = ?
              AND ativo = 1
            ORDER BY etapa_ensino, nome
        """, (id,))

        componentes_banco = cursor.fetchall()

        componentes_salvos = [
            {
                "id": componente["id"],
                "escola_id": componente["escola_id"],
                "etapa": componente["etapa_ensino"],
                "nome": componente["nome"],
                "tipo": componente["tipo"] or "padrao",
                "ativo": componente["ativo"]
            }
            for componente in componentes_banco
        ]

        return render_template(
            "gestao/editar_instituicao.html",
            escola=escola,
            administrador=administrador,
            modalidades_marcadas=modalidades_marcadas,
            etapas_marcadas=etapas_marcadas,
            componentes_salvos=componentes_salvos
        )

    except sqlite3.IntegrityError as erro:
        banco.rollback()

        import traceback
        traceback.print_exc()

        print(
            "ERRO DE INTEGRIDADE AO EDITAR INSTITUIÇÃO:",
            repr(erro)
        )

        flash(
            f"Não foi possível salvar as alterações: {erro}",
            "erro"
        )

        return redirect(f"/gestao/instituicoes/editar/{id}")

    except Exception as erro:
        banco.rollback()

        import traceback
        traceback.print_exc()

        print(
            "ERRO COMPLETO AO EDITAR INSTITUIÇÃO:",
            repr(erro)
        )

        flash(
            f"Ocorreu um erro ao salvar as alterações: {erro}",
            "erro"
        )

        return redirect(f"/gestao/instituicoes/editar/{id}")

    finally:
        banco.close()

@app.route("/gestao/instituicoes/ver/<int:id>")
def ver_instituicao(id):

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    # ======================================================
    # INSTITUIÇÃO
    # ======================================================

    cursor.execute("""
        SELECT *
        FROM escolas
        WHERE id = ?
    """, (id,))

    escola = cursor.fetchone()

    if escola is None:
        banco.close()
        return redirect("/gestao/instituicoes")

    # ======================================================
    # ADMINISTRADOR DA INSTITUIÇÃO
    # ======================================================

    cursor.execute("""
        SELECT
            usuarios.id,
            usuarios.nome,
            usuarios.email,
            usuarios.cpf,
            usuarios.ativo,
            cargos.nome AS cargo
        FROM usuarios
        LEFT JOIN cargos
            ON usuarios.cargo_id = cargos.id
        WHERE usuarios.escola_id = ?
          AND cargos.nome = 'Administrador da Instituição'
        ORDER BY usuarios.id
        LIMIT 1
    """, (id,))

    administrador = cursor.fetchone()

    # ======================================================
    # TODOS OS USUÁRIOS VINCULADOS À INSTITUIÇÃO
    # ======================================================

    cursor.execute("""
        SELECT
            usuarios.id,
            usuarios.nome,
            usuarios.email,
            usuarios.cpf,
            usuarios.ativo,
            cargos.nome AS cargo
        FROM usuarios
        LEFT JOIN cargos
            ON usuarios.cargo_id = cargos.id
        WHERE usuarios.escola_id = ?
        ORDER BY
            usuarios.ativo DESC,
            cargos.nome,
            usuarios.nome
    """, (id,))

    usuarios_instituicao = cursor.fetchall()

    # ======================================================
    # COMPONENTES CURRICULARES
    # ======================================================

    cursor.execute("""
        SELECT
            id,
            escola_id,
            etapa_ensino,
            nome,
            tipo,
            ativo
        FROM componentes_curriculares
        WHERE escola_id = ?
          AND ativo = 1
        ORDER BY etapa_ensino, nome
    """, (id,))

    componentes = cursor.fetchall()

    componentes_salvos = []

    for componente in componentes:

        componentes_salvos.append({
            "id": componente["id"],
            "escola_id": componente["escola_id"],
            "etapa": componente["etapa_ensino"],
            "nome": componente["nome"],
            "tipo": componente["tipo"],
            "ativo": componente["ativo"]
        })

    banco.close()

    return render_template(
        "gestao/ver_instituicao.html",
        escola=escola,
        administrador=administrador,
        usuarios_instituicao=usuarios_instituicao,
        componentes_salvos=componentes_salvos
    )

@app.route("/gestao/instituicoes/inativar/<int:id>")
def inativar_instituicao(id):

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        UPDATE escolas
        SET status = 0
        WHERE id = ?
    """, (id,))

    cursor.execute("""
        UPDATE usuarios
        SET ativo = 0
        WHERE escola_id = ?
    """, (id,))

    banco.commit()
    banco.close()

    flash("Instituição inativada com sucesso.", "success")

    return redirect("/gestao/instituicoes")

@app.route("/gestao/instituicoes/ativar/<int:id>")
def ativar_instituicao(id):

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        UPDATE escolas
        SET status = 1
        WHERE id = ?
    """, (id,))

    cursor.execute("""
        UPDATE usuarios
        SET ativo = 1
        WHERE escola_id = ?
    """, (id,))

    banco.commit()
    banco.close()

    flash("Instituição ativada com sucesso.", "success")

    return redirect("/gestao/instituicoes")

@app.route("/gestao/instituicoes/excluir/<int:id>")
def excluir_instituicao(id):

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    try:
        cursor.execute("""
            DELETE FROM usuarios
            WHERE escola_id = ?
        """, (id,))

        cursor.execute("""
            DELETE FROM escolas
            WHERE id = ?
        """, (id,))

        banco.commit()

        flash("Instituição excluída com sucesso.", "success")

    except Exception as erro:
        banco.rollback()

        print(f"Erro ao excluir instituição: {erro}")

        flash(
            "Não foi possível excluir a instituição.",
            "erro"
        )

    finally:
        banco.close()

    return redirect("/gestao/instituicoes")

@app.route("/excluir_usuario/<int:id>")
def excluir_usuario(id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/login")

    if id == session.get("usuario_id"):
        flash(
            "Você não pode excluir o próprio usuário conectado.",
            "erro"
        )
        return redirect("/usuarios")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo_logado = session.get("usuario_cargo")
    escola_id = session.get("escola_id")

    if cargo_logado == "Administrador Geral":
        cursor.execute("""
            SELECT id
            FROM usuarios
            WHERE id = ?
            LIMIT 1
        """, (id,))
    else:
        cursor.execute("""
            SELECT id
            FROM usuarios
            WHERE id = ?
              AND escola_id = ?
            LIMIT 1
        """, (
            id,
            escola_id
        ))

    usuario = cursor.fetchone()

    if usuario is None:
        banco.close()

        flash(
            "Usuário não encontrado ou sem permissão para excluir.",
            "erro"
        )

        return redirect("/usuarios")

    try:
        cursor.execute("""
            DELETE FROM coordenador_turmas
            WHERE usuario_id = ?
        """, (id,))

        cursor.execute("""
            DELETE FROM usuarios
            WHERE id = ?
        """, (id,))

        banco.commit()

        flash(
            "Usuário excluído com sucesso.",
            "success"
        )

    except Exception as erro:
        banco.rollback()

        print(f"Erro ao excluir usuário: {erro}")

        flash(
            "Não foi possível excluir o usuário.",
            "erro"
        )

    finally:
        banco.close()

    return redirect("/usuarios")

# =========================================================
# API - COMPONENTES CURRICULARES DA TURMA
# =========================================================

@app.route(
    "/api/turmas/<int:turma_id>/componentes",
    methods=["GET"]
)
def api_componentes_da_turma(turma_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return jsonify({
            "sucesso": False,
            "mensagem": "Você não possui permissão para consultar componentes.",
            "componentes": []
        }), 403

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cargo_logado = session.get(
            "usuario_cargo",
            ""
        ).strip()

        escola_logada_id = session.get("escola_id")

        if escola_logada_id not in (None, ""):
            try:
                escola_logada_id = int(escola_logada_id)
            except (TypeError, ValueError):
                escola_logada_id = None

        cursor.execute("""
            SELECT
                id,
                escola_id,
                etapa,
                ano,
                nome,
                turno
            FROM turmas
            WHERE id = ?
            LIMIT 1
        """, (turma_id,))

        turma = cursor.fetchone()

        if turma is None:
            return jsonify({
                "sucesso": False,
                "mensagem": "Turma não encontrada.",
                "componentes": []
            }), 404

        if (
            cargo_logado == "Administrador da Instituição"
            and turma["escola_id"] != escola_logada_id
        ):
            return jsonify({
                "sucesso": False,
                "mensagem": "Você não possui acesso a esta turma.",
                "componentes": []
            }), 403

        etapa_turma = (turma["etapa"] or "").strip()

        if not etapa_turma:
            return jsonify({
                "sucesso": False,
                "mensagem": "A turma não possui uma etapa de ensino definida.",
                "componentes": []
            }), 400

        cursor.execute("""
            SELECT
                MIN(id) AS id,
                TRIM(nome) AS nome,
                TRIM(etapa_ensino) AS etapa_ensino
            FROM componentes_curriculares
            WHERE escola_id = ?
              AND ativo = 1
              AND TRIM(COALESCE(nome, '')) <> ''
              AND LOWER(TRIM(etapa_ensino)) = LOWER(TRIM(?))
            GROUP BY
                LOWER(TRIM(nome)),
                LOWER(TRIM(etapa_ensino))
            ORDER BY
                TRIM(nome) COLLATE NOCASE ASC
        """, (
            turma["escola_id"],
            etapa_turma
        ))

        componentes = [
            {
                "id": componente["id"],
                "nome": componente["nome"],
                "etapa_ensino": componente["etapa_ensino"]
            }
            for componente in cursor.fetchall()
        ]

        return jsonify({
            "sucesso": True,
            "turma": {
                "id": turma["id"],
                "etapa": turma["etapa"],
                "ano": turma["ano"],
                "nome": turma["nome"],
                "turno": turma["turno"]
            },
            "componentes": componentes
        })

    except sqlite3.Error as erro:
        print("ERRO AO BUSCAR COMPONENTES DA TURMA:", erro)

        return jsonify({
            "sucesso": False,
            "mensagem": "Não foi possível carregar os componentes curriculares.",
            "componentes": []
        }), 500

    finally:
        banco.close()


# =====================================================
# VÍNCULOS DO PROFESSOR
# =====================================================

@app.route(
    "/professor/<int:professor_id>/vinculos",
    methods=["GET", "POST"]
)
def professor_vinculos(professor_id):

    # Apenas administradores podem gerenciar vínculos
    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        flash(
            "Você não possui permissão para acessar os vínculos.",
            "erro"
        )
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        cursor.execute("PRAGMA foreign_keys = ON")

        cargo_logado = session.get(
            "usuario_cargo",
            ""
        ).strip()

        escola_logada_id = session.get("escola_id")

        # =================================================
        # BUSCA O PROFESSOR
        # =================================================

        cursor.execute("""
            SELECT
                usuarios.id,
                usuarios.nome,
                usuarios.email,
                usuarios.escola_id,
                usuarios.ativo,
                cargos.nome AS cargo,
                escolas.nome_instituicao
            FROM usuarios

            INNER JOIN cargos
                ON cargos.id = usuarios.cargo_id

            LEFT JOIN escolas
                ON escolas.id = usuarios.escola_id

            WHERE usuarios.id = ?
            LIMIT 1
        """, (
            professor_id,
        ))

        professor = cursor.fetchone()

        if professor is None:
            flash(
                "Professor não encontrado.",
                "erro"
            )
            return redirect("/usuarios")

        # Confirma que o usuário selecionado é professor
        if professor["cargo"] != "Professor":
            flash(
                "O usuário selecionado não possui o cargo Professor.",
                "erro"
            )
            return redirect("/usuarios")

        # Confirma que o professor está ativo
        if professor["ativo"] != 1:
            flash(
                "O professor selecionado está inativo.",
                "erro"
            )
            return redirect("/usuarios")

        escola_professor_id = professor["escola_id"]

        if not escola_professor_id:
            flash(
                "O professor não está vinculado a uma instituição.",
                "erro"
            )
            return redirect("/usuarios")

        # Administrador da instituição só acessa professores
        # da própria instituição
        if (
            cargo_logado == "Administrador da Instituição"
            and escola_professor_id != escola_logada_id
        ):
            flash(
                "Você não possui permissão para acessar esse professor.",
                "erro"
            )
            return redirect("/usuarios")

        # =================================================
        # SALVA UM NOVO VÍNCULO
        # =================================================

        if request.method == "POST":

            turma_id = request.form.get(
                "turma_id",
                ""
            ).strip()

            componente_id = request.form.get(
                "componente_id",
                ""
            ).strip()

            if not turma_id:
                flash(
                    "Selecione uma turma.",
                    "erro"
                )
                return redirect(
                    f"/professor/{professor_id}/vinculos"
                )

            if not componente_id:
                flash(
                    "Selecione um componente curricular.",
                    "erro"
                )
                return redirect(
                    f"/professor/{professor_id}/vinculos"
                )

            # Confirma que a turma pertence à mesma instituição
            cursor.execute("""
                SELECT
                    id,
                    etapa
                FROM turmas
                WHERE id = ?
                  AND escola_id = ?
                LIMIT 1
            """, (
                turma_id,
                escola_professor_id
            ))

            turma = cursor.fetchone()

            if turma is None:
                flash(
                    "A turma selecionada não pertence à instituição do professor.",
                    "erro"
                )
                return redirect(
                    f"/professor/{professor_id}/vinculos"
                )

            # Confirma que o componente pertence à mesma instituição
            cursor.execute("""
                SELECT
                    id,
                    TRIM(nome) AS nome,
                    TRIM(etapa_ensino) AS etapa_ensino
                FROM componentes_curriculares
                WHERE id = ?
                  AND escola_id = ?
                  AND ativo = 1
                  AND LOWER(TRIM(etapa_ensino)) =
                      LOWER(TRIM(?))
                LIMIT 1
            """, (
                componente_id,
                escola_professor_id,
                turma["etapa"]
            ))

            componente = cursor.fetchone()

            if componente is None:
                flash(
                    "O componente curricular selecionado é inválido.",
                    "erro"
                )
                return redirect(
                    f"/professor/{professor_id}/vinculos"
                )

            # Verifica vínculo duplicado pelo nome normalizado.
            # Dessa forma, registros antigos do mesmo componente com IDs
            # diferentes não geram vínculos visualmente repetidos.
            cursor.execute("""
                SELECT pv.id
                FROM professor_vinculos AS pv
                INNER JOIN componentes_curriculares AS cc
                    ON cc.id = pv.componente_id
                WHERE pv.professor_id = ?
                  AND pv.turma_id = ?
                  AND LOWER(TRIM(cc.nome)) = LOWER(TRIM(?))
                LIMIT 1
            """, (
                professor_id,
                turma_id,
                componente["nome"]
            ))

            vinculo_existente = cursor.fetchone()

            if vinculo_existente:
                flash(
                    "Esse vínculo já está cadastrado.",
                    "erro"
                )
                return redirect(
                    f"/professor/{professor_id}/vinculos"
                )

            cursor.execute("""
                INSERT INTO professor_vinculos (
                    professor_id,
                    turma_id,
                    componente_id
                )
                VALUES (?, ?, ?)
            """, (
                professor_id,
                turma_id,
                componente_id
            ))

            banco.commit()

            flash(
                "Vínculo cadastrado com sucesso.",
                "success"
            )

            return redirect(
                f"/professor/{professor_id}/vinculos"
            )

        # =================================================
        # LISTA AS TURMAS DA INSTITUIÇÃO
        # =================================================

        cursor.execute("""
            SELECT
                id,
                etapa,
                ano,
                nome,
                turno
            FROM turmas
            WHERE escola_id = ?
            ORDER BY
                etapa COLLATE NOCASE ASC,
                CAST(ano AS INTEGER) ASC,
                nome COLLATE NOCASE ASC,
                turno COLLATE NOCASE ASC
        """, (
            escola_professor_id,
        ))

        turmas = cursor.fetchall()

        # =================================================
        # COMPONENTES CARREGADOS APÓS SELECIONAR A TURMA
        # =================================================

        componentes = []

        # =================================================
        # LISTA OS VÍNCULOS DO PROFESSOR
        # =================================================

        cursor.execute("""
            SELECT
                professor_vinculos.id,

                turmas.id AS turma_id,
                turmas.etapa,
                turmas.ano,
                turmas.nome AS turma_nome,
                turmas.turno,

                componentes_curriculares.id AS componente_id,
                componentes_curriculares.nome AS componente_nome

            FROM professor_vinculos

            INNER JOIN turmas
                ON turmas.id = professor_vinculos.turma_id

            INNER JOIN componentes_curriculares
                ON componentes_curriculares.id =
                   professor_vinculos.componente_id

            WHERE professor_vinculos.professor_id = ?

            ORDER BY
                turmas.etapa COLLATE NOCASE ASC,
                CAST(turmas.ano AS INTEGER) ASC,
                turmas.nome COLLATE NOCASE ASC,
                componentes_curriculares.nome COLLATE NOCASE ASC
        """, (
            professor_id,
        ))

        vinculos = cursor.fetchall()

        return render_template(
            "gestao/professor_vinculos.html",
            professor=professor,
            turmas=turmas,
            componentes=componentes,
            vinculos=vinculos
        )

    except sqlite3.Error as erro:

        banco.rollback()

        print(
            "ERRO NOS VÍNCULOS DO PROFESSOR:",
            erro
        )

        flash(
            f"Erro ao carregar os vínculos: {erro}",
            "erro"
        )

        return redirect("/usuarios")

    finally:
        banco.close()


# =====================================================
# EXCLUIR VÍNCULO DO PROFESSOR
# =====================================================

@app.route(
    "/professor/<int:professor_id>/vinculos/<int:vinculo_id>/excluir",
    methods=["POST"]
)
def excluir_professor_vinculo(
    professor_id,
    vinculo_id
):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        flash(
            "Você não possui permissão para excluir vínculos.",
            "erro"
        )
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        cargo_logado = session.get(
            "usuario_cargo",
            ""
        ).strip()

        escola_logada_id = session.get("escola_id")

        # Busca o vínculo e a instituição do professor
        cursor.execute("""
            SELECT
                professor_vinculos.id,
                usuarios.escola_id

            FROM professor_vinculos

            INNER JOIN usuarios
                ON usuarios.id =
                   professor_vinculos.professor_id

            WHERE professor_vinculos.id = ?
              AND professor_vinculos.professor_id = ?

            LIMIT 1
        """, (
            vinculo_id,
            professor_id
        ))

        vinculo = cursor.fetchone()

        if vinculo is None:
            flash(
                "Vínculo não encontrado.",
                "erro"
            )
            return redirect(
                f"/professor/{professor_id}/vinculos"
            )

        if (
            cargo_logado == "Administrador da Instituição"
            and vinculo["escola_id"] != escola_logada_id
        ):
            flash(
                "Você não possui permissão para excluir esse vínculo.",
                "erro"
            )
            return redirect("/usuarios")

        cursor.execute("""
            DELETE FROM professor_vinculos
            WHERE id = ?
              AND professor_id = ?
        """, (
            vinculo_id,
            professor_id
        ))

        banco.commit()

        flash(
            "Vínculo excluído com sucesso.",
            "success"
        )

    except sqlite3.Error as erro:

        banco.rollback()

        print(
            "ERRO AO EXCLUIR VÍNCULO:",
            erro
        )

        flash(
            f"Erro ao excluir o vínculo: {erro}",
            "erro"
        )

    finally:
        banco.close()

    return redirect(
        f"/professor/{professor_id}/vinculos"
    )

# =====================================================
# COMPONENTES CURRICULARES
# =====================================================

@app.route("/componentes_curriculares", methods=["GET", "POST"])
def componentes_curriculares():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador"
    ]):
        flash(
            "Você não possui permissão para acessar os componentes curriculares.",
            "erro"
        )
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        usuario_cargo = session.get(
            "usuario_cargo",
            ""
        ).strip()

        usuario_id = session.get("usuario_id")
        escola_id = session.get("escola_id")

        # =================================================
        # RECUPERA A INSTITUIÇÃO DO USUÁRIO
        # =================================================

        if (
            usuario_cargo != "Administrador Geral"
            and not escola_id
            and usuario_id
        ):

            cursor.execute("""
                SELECT escola_id
                FROM usuarios
                WHERE id = ?
                LIMIT 1
            """, (
                usuario_id,
            ))

            usuario = cursor.fetchone()

            if usuario and usuario["escola_id"]:
                escola_id = usuario["escola_id"]
                session["escola_id"] = escola_id

        # =================================================
        # CADASTRA NOVO COMPONENTE
        # =================================================

        if request.method == "POST":

            nome = request.form.get(
                "nome",
                ""
            ).strip()

            escola_formulario = request.form.get(
                "escola_id",
                ""
            ).strip()

            if usuario_cargo == "Administrador Geral":
                escola_cadastro_id = escola_formulario
            else:
                escola_cadastro_id = escola_id

            if not escola_cadastro_id:
                flash(
                    "Selecione uma instituição.",
                    "erro"
                )
                return redirect("/componentes_curriculares")

            if not nome:
                flash(
                    "Informe o nome do componente curricular.",
                    "erro"
                )
                return redirect("/componentes_curriculares")

            cursor.execute("""
                SELECT id
                FROM escolas
                WHERE id = ?
                  AND COALESCE(status, 1) = 1
                LIMIT 1
            """, (
                escola_cadastro_id,
            ))

            escola = cursor.fetchone()

            if escola is None:
                flash(
                    "A instituição selecionada é inválida ou está inativa.",
                    "erro"
                )
                return redirect("/componentes_curriculares")

            cursor.execute("""
                SELECT id
                FROM componentes_curriculares
                WHERE escola_id = ?
                  AND LOWER(TRIM(nome)) = LOWER(TRIM(?))
                LIMIT 1
            """, (
                escola_cadastro_id,
                nome
            ))

            componente_existente = cursor.fetchone()

            if componente_existente:
                flash(
                    "Esse componente curricular já está cadastrado.",
                    "erro"
                )
                return redirect("/componentes_curriculares")

            cursor.execute("""
                INSERT INTO componentes_curriculares (
                    escola_id,
                    nome,
                    ativo,
                    criado_em
                )
                VALUES (?, ?, 1, ?)
            """, (
                escola_cadastro_id,
                nome,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

            banco.commit()

            flash(
                "Componente curricular cadastrado com sucesso.",
                "success"
            )

            return redirect("/componentes_curriculares")

        # =================================================
        # LISTA INSTITUIÇÕES PARA ADMINISTRADOR GERAL
        # =================================================

        escolas = []

        if usuario_cargo == "Administrador Geral":

            cursor.execute("""
                SELECT
                    id,
                    nome_instituicao
                FROM escolas
                WHERE COALESCE(status, 1) = 1
                ORDER BY nome_instituicao COLLATE NOCASE ASC
            """)

            escolas = cursor.fetchall()

        # =================================================
        # LISTA COMPONENTES
        # =================================================

        if usuario_cargo == "Administrador Geral":

            cursor.execute("""
                SELECT
                    componentes_curriculares.id,
                    componentes_curriculares.nome,
                    componentes_curriculares.ativo,
                    componentes_curriculares.escola_id,
                    escolas.nome_instituicao

                FROM componentes_curriculares

                INNER JOIN escolas
                    ON escolas.id =
                       componentes_curriculares.escola_id

                ORDER BY
                    escolas.nome_instituicao COLLATE NOCASE ASC,
                    componentes_curriculares.nome COLLATE NOCASE ASC
            """)

        else:

            if not escola_id:
                flash(
                    "Seu usuário não está vinculado a uma instituição.",
                    "erro"
                )
                return redirect("/")

            cursor.execute("""
                SELECT
                    componentes_curriculares.id,
                    componentes_curriculares.nome,
                    componentes_curriculares.ativo,
                    componentes_curriculares.escola_id,
                    escolas.nome_instituicao

                FROM componentes_curriculares

                INNER JOIN escolas
                    ON escolas.id =
                       componentes_curriculares.escola_id

                WHERE componentes_curriculares.escola_id = ?

                ORDER BY
                    componentes_curriculares.nome COLLATE NOCASE ASC
            """, (
                escola_id,
            ))

        componentes = cursor.fetchall()

        return render_template(
            "gestao/componentes_curriculares.html",
            componentes=componentes,
            escolas=escolas,
            usuario_cargo=usuario_cargo
        )

    except sqlite3.Error as erro:

        banco.rollback()

        print(
            "ERRO NOS COMPONENTES CURRICULARES:",
            erro
        )

        flash(
            f"Erro ao carregar os componentes curriculares: {erro}",
            "erro"
        )

        return redirect("/")

    finally:
        banco.close()


# =====================================================
# ALTERAR STATUS DO COMPONENTE
# =====================================================

@app.route(
    "/componentes_curriculares/<int:componente_id>/status",
    methods=["POST"]
)
def alterar_status_componente(componente_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador"
    ]):
        flash(
            "Você não possui permissão para alterar componentes curriculares.",
            "erro"
        )
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        usuario_cargo = session.get(
            "usuario_cargo",
            ""
        ).strip()

        escola_id = session.get("escola_id")

        cursor.execute("""
            SELECT
                id,
                escola_id,
                ativo
            FROM componentes_curriculares
            WHERE id = ?
            LIMIT 1
        """, (
            componente_id,
        ))

        componente = cursor.fetchone()

        if componente is None:
            flash(
                "Componente curricular não encontrado.",
                "erro"
            )
            return redirect("/componentes_curriculares")

        if (
            usuario_cargo != "Administrador Geral"
            and componente["escola_id"] != escola_id
        ):
            flash(
                "Você não possui permissão para alterar esse componente.",
                "erro"
            )
            return redirect("/componentes_curriculares")

        novo_status = 0 if componente["ativo"] == 1 else 1

        cursor.execute("""
            UPDATE componentes_curriculares
            SET ativo = ?
            WHERE id = ?
        """, (
            novo_status,
            componente_id
        ))

        banco.commit()

        if novo_status == 1:
            flash(
                "Componente curricular ativado com sucesso.",
                "success"
            )
        else:
            flash(
                "Componente curricular desativado com sucesso.",
                "success"
            )

    except sqlite3.Error as erro:

        banco.rollback()

        print(
            "ERRO AO ALTERAR COMPONENTE:",
            erro
        )

        flash(
            f"Erro ao alterar o componente curricular: {erro}",
            "erro"
        )

    finally:
        banco.close()

    return redirect("/componentes_curriculares")


# =====================================================
# EXCLUIR COMPONENTE
# =====================================================

# =========================================================
# LISTAR ANOS LETIVOS
# =========================================================

@app.route("/anos-letivos")
def anos_letivos():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = session.get("usuario_cargo", "").strip()
    escola_id = obter_escola_usuario()

    lista_anos = []
    escolas = []
    configuracao_global = None
    configuracao_instituicao = None
    proximos_anos = {}
    ano_sugerido = datetime.now().year + 1

    try:
        cursor.execute("""
            SELECT *
            FROM configuracao_ano_letivo_global
            WHERE id = 1
            LIMIT 1
        """)
        configuracao_global = cursor.fetchone()

        if cargo == "Administrador Geral":
            cursor.execute("""
                SELECT id, nome_instituicao
                FROM escolas
                WHERE COALESCE(status, 1) = 1
                ORDER BY nome_instituicao COLLATE NOCASE ASC
            """)
            escolas = cursor.fetchall()

            for escola in escolas:
                cursor.execute("""
                    SELECT COALESCE(MAX(ano), ?) + 1 AS proximo
                    FROM anos_letivos
                    WHERE escola_id = ?
                """, (datetime.now().year - 1, escola["id"]))
                proximo = cursor.fetchone()
                proximos_anos[str(escola["id"])] = (
                    proximo["proximo"] if proximo else datetime.now().year
                )

            cursor.execute("""
                SELECT
                    al.id,
                    al.escola_id,
                    al.ano,
                    al.data_inicio,
                    al.data_fim,
                    al.ativo,
                    al.encerrado,
                    al.criado_em,
                    e.nome_instituicao,

                    (SELECT COUNT(*) FROM turmas t
                     WHERE t.ano_letivo_id = al.id) AS total_turmas,

                    (
                        SELECT COUNT(*)
                        FROM (
                            SELECT am.aluno_id
                            FROM aluno_matriculas am
                            WHERE am.ano_letivo_id = al.id
                            UNION
                            SELECT a.id
                            FROM alunos a
                            WHERE a.ano_letivo_id = al.id
                        )
                    ) AS total_alunos,

                    (SELECT COUNT(*) FROM provas p
                     WHERE p.ano_letivo_id = al.id) AS total_provas,

                    (
                        SELECT COUNT(DISTINCT pv.professor_id)
                        FROM professor_vinculos pv
                        INNER JOIN turmas t_prof ON t_prof.id = pv.turma_id
                        WHERE t_prof.ano_letivo_id = al.id
                    ) AS total_professores

                FROM anos_letivos al
                INNER JOIN escolas e ON e.id = al.escola_id
                ORDER BY e.nome_instituicao COLLATE NOCASE ASC, al.ano DESC
            """)
            lista_anos = cursor.fetchall()

        else:
            if not escola_id:
                flash("Não foi possível identificar sua instituição.", "erro")
                return redirect("/")

            atualizar_ano_letivo_na_sessao(escola_id)

            cursor.execute("""
                SELECT *
                FROM configuracao_ano_letivo_instituicao
                WHERE escola_id = ?
                LIMIT 1
            """, (escola_id,))
            configuracao_instituicao = cursor.fetchone()

            cursor.execute("""
                SELECT COALESCE(MAX(ano), ?) + 1 AS proximo
                FROM anos_letivos
                WHERE escola_id = ?
            """, (datetime.now().year - 1, escola_id))
            proximo = cursor.fetchone()
            ano_sugerido = proximo["proximo"] if proximo else datetime.now().year

            cursor.execute("""
                SELECT
                    al.id,
                    al.escola_id,
                    al.ano,
                    al.data_inicio,
                    al.data_fim,
                    al.ativo,
                    al.encerrado,
                    al.criado_em,
                    e.nome_instituicao,

                    (SELECT COUNT(*) FROM turmas t
                     WHERE t.ano_letivo_id = al.id) AS total_turmas,

                    (
                        SELECT COUNT(*)
                        FROM (
                            SELECT am.aluno_id
                            FROM aluno_matriculas am
                            WHERE am.ano_letivo_id = al.id
                            UNION
                            SELECT a.id
                            FROM alunos a
                            WHERE a.ano_letivo_id = al.id
                        )
                    ) AS total_alunos,

                    (SELECT COUNT(*) FROM provas p
                     WHERE p.ano_letivo_id = al.id) AS total_provas,

                    (
                        SELECT COUNT(DISTINCT pv.professor_id)
                        FROM professor_vinculos pv
                        INNER JOIN turmas t_prof ON t_prof.id = pv.turma_id
                        WHERE t_prof.ano_letivo_id = al.id
                    ) AS total_professores

                FROM anos_letivos al
                INNER JOIN escolas e ON e.id = al.escola_id
                WHERE al.escola_id = ?
                ORDER BY al.ano DESC
            """, (escola_id,))
            lista_anos = cursor.fetchall()

        return render_template(
            "gestao/anos_letivos.html",
            anos_letivos=lista_anos,
            escolas=escolas,
            cargo=cargo,
            configuracao_global=configuracao_global,
            configuracao_instituicao=configuracao_instituicao,
            proximos_anos=proximos_anos,
            ano_sugerido=ano_sugerido
        )

    except sqlite3.Error as erro:
        import traceback
        traceback.print_exc()
        flash(f"Erro ao carregar os anos letivos: {erro}", "erro")

        return render_template(
            "gestao/anos_letivos.html",
            anos_letivos=[],
            escolas=escolas,
            cargo=cargo,
            configuracao_global=configuracao_global,
            configuracao_instituicao=configuracao_instituicao,
            proximos_anos=proximos_anos,
            ano_sugerido=ano_sugerido
        )

    finally:
        banco.close()


# =========================================================
# ABRIR NOVO ANO LETIVO
# =========================================================

@app.route("/anos-letivos/abrir", methods=["POST"])
def abrir_ano_letivo():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/acesso_negado")

    cargo = session.get("usuario_cargo", "").strip()

    if cargo == "Administrador Geral":
        escola_id = request.form.get("escola_id", "").strip()
    else:
        escola_id = obter_escola_usuario()

    ano = request.form.get("ano", "").strip()
    data_inicio = request.form.get("data_inicio", "").strip()
    data_fim = request.form.get("data_fim", "").strip()

    copiar_turmas = request.form.get("copiar_turmas") == "1"
    copiar_vinculos = (
        copiar_turmas
        and request.form.get("copiar_vinculos") == "1"
    )

    if not escola_id:
        flash("Selecione uma instituição.", "erro")
        return redirect("/anos-letivos")

    try:
        escola_id = int(escola_id)
    except (TypeError, ValueError):
        flash("A instituição selecionada é inválida.", "erro")
        return redirect("/anos-letivos")

    try:
        ano = int(ano)
    except (TypeError, ValueError):
        flash("Informe um ano letivo válido.", "erro")
        return redirect("/anos-letivos")

    if ano < 2000 or ano > 2100:
        flash("Informe um ano entre 2000 e 2100.", "erro")
        return redirect("/anos-letivos")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        cursor.execute("""
            SELECT id, nome_instituicao
            FROM escolas
            WHERE id = ?
              AND COALESCE(status, 1) = 1
            LIMIT 1
        """, (
            escola_id,
        ))

        escola = cursor.fetchone()

        if escola is None:
            flash(
                "A instituição não existe ou está inativa.",
                "erro"
            )
            return redirect("/anos-letivos")

        cursor.execute("""
            SELECT id
            FROM anos_letivos
            WHERE escola_id = ?
              AND ano = ?
            LIMIT 1
        """, (
            escola_id,
            ano
        ))

        if cursor.fetchone():
            flash(
                f"O ano letivo {ano} já está cadastrado.",
                "erro"
            )
            return redirect("/anos-letivos")

        # Guarda o ano anterior para copiar as turmas.
        cursor.execute("""
            SELECT id, ano
            FROM anos_letivos
            WHERE escola_id = ?
              AND ativo = 1
              AND encerrado = 0
            ORDER BY ano DESC
            LIMIT 1
        """, (
            escola_id,
        ))

        ano_anterior = cursor.fetchone()

        # Encerra o ano ativo atual.
        cursor.execute("""
            UPDATE anos_letivos
            SET
                ativo = 0,
                encerrado = 1
            WHERE escola_id = ?
              AND ativo = 1
        """, (
            escola_id,
        ))

        # Cria o novo ano.
        cursor.execute("""
            INSERT INTO anos_letivos (
                escola_id,
                ano,
                data_inicio,
                data_fim,
                ativo,
                encerrado
            )
            VALUES (?, ?, ?, ?, 1, 0)
        """, (
            escola_id,
            ano,
            data_inicio or None,
            data_fim or None
        ))

        novo_ano_id = cursor.lastrowid

        # Copia as turmas do ano anterior, quando solicitado.
        total_turmas_copiadas = 0
        total_vinculos_copiados = 0
        mapa_turmas = {}

        if copiar_turmas and ano_anterior:
            cursor.execute("""
                SELECT id, nome, etapa, ano, turno
                FROM turmas
                WHERE escola_id = ?
                  AND ano_letivo_id = ?
                ORDER BY id
            """, (escola_id, ano_anterior["id"]))

            turmas_anteriores = cursor.fetchall()

            for turma in turmas_anteriores:
                cursor.execute("""
                    INSERT INTO turmas (
                        nome,
                        etapa,
                        ano,
                        turno,
                        escola_id,
                        ano_letivo,
                        ano_letivo_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    turma["nome"],
                    turma["etapa"],
                    turma["ano"],
                    turma["turno"],
                    escola_id,
                    ano,
                    novo_ano_id
                ))

                nova_turma_id = cursor.lastrowid
                mapa_turmas[turma["id"]] = nova_turma_id
                total_turmas_copiadas += 1

            if copiar_vinculos and mapa_turmas:
                for turma_antiga_id, turma_nova_id in mapa_turmas.items():
                    cursor.execute("""
                        SELECT professor_id, componente_id
                        FROM professor_vinculos
                        WHERE turma_id = ?
                    """, (turma_antiga_id,))

                    for vinculo in cursor.fetchall():
                        cursor.execute("""
                            INSERT OR IGNORE INTO professor_vinculos (
                                professor_id,
                                turma_id,
                                componente_id
                            )
                            VALUES (?, ?, ?)
                        """, (
                            vinculo["professor_id"],
                            turma_nova_id,
                            vinculo["componente_id"]
                        ))
                        if cursor.rowcount:
                            total_vinculos_copiados += 1

        # Mantém o campo antigo sincronizado.
        cursor.execute("""
            UPDATE escolas
            SET ano_letivo = ?
            WHERE id = ?
        """, (
            ano,
            escola_id
        ))

        banco.commit()

        # Remove eventual consulta a um ano antigo.
        session.pop("ano_letivo_selecionado_id", None)

        if cargo != "Administrador Geral":
            session["ano_letivo_id"] = novo_ano_id
            session["ano_letivo"] = ano
            session["ano_letivo_visualizado"] = ano
        else:
            session["ano_letivo_visualizado"] = ano
            session["ano_letivo"] = ano

        mensagem = f"Ano letivo {ano} aberto com sucesso."

        if copiar_turmas:
            mensagem += f" {total_turmas_copiadas} turma(s) foram copiadas."

        if copiar_vinculos:
            mensagem += (
                f" {total_vinculos_copiados} vínculo(s) de professor "
                "foram copiados."
            )

        cursor.execute("""
            INSERT INTO ano_letivo_auditoria (
                escola_id,
                ano_letivo_id,
                usuario_id,
                acao,
                detalhes
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            escola_id,
            novo_ano_id,
            session.get("usuario_id"),
            "ABRIR_ANO",
            f"Ano {ano} aberto manualmente."
        ))
        banco.commit()

        flash(mensagem, "success")

        return redirect("/anos-letivos")

    except sqlite3.Error as erro:

        banco.rollback()

        import traceback
        traceback.print_exc()

        print("ERRO AO ABRIR ANO LETIVO:", erro)

        flash(
            f"Erro ao abrir o ano letivo: {erro}",
            "erro"
        )

        return redirect("/anos-letivos")

    finally:
        banco.close()


# =========================================================
# CONSULTAR UM ANO LETIVO
# =========================================================

@app.route(
    "/anos-letivos/<int:ano_letivo_id>/selecionar",
    methods=["POST"]
)
def selecionar_ano_letivo(ano_letivo_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/acesso_negado")

    cargo = session.get("usuario_cargo", "").strip()
    escola_id = obter_escola_usuario()

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        if cargo == "Administrador Geral":

            cursor.execute("""
                SELECT id, escola_id, ano
                FROM anos_letivos
                WHERE id = ?
                LIMIT 1
            """, (
                ano_letivo_id,
            ))

        else:

            cursor.execute("""
                SELECT id, escola_id, ano
                FROM anos_letivos
                WHERE id = ?
                  AND escola_id = ?
                LIMIT 1
            """, (
                ano_letivo_id,
                escola_id
            ))

        ano_letivo = cursor.fetchone()

        if ano_letivo is None:
            flash(
                "Ano letivo não encontrado ou sem permissão.",
                "erro"
            )
            return redirect("/anos-letivos")

        if cargo == "Administrador Geral":
            session["ano_letivo_visualizado"] = ano_letivo["ano"]
            session["ano_letivo"] = ano_letivo["ano"]
            session.pop("ano_letivo_id", None)
            session.pop("ano_letivo_selecionado_id", None)
        else:
            session["ano_letivo_selecionado_id"] = ano_letivo["id"]
            session["ano_letivo_id"] = ano_letivo["id"]
            session["ano_letivo"] = ano_letivo["ano"]
            session["ano_letivo_visualizado"] = ano_letivo["ano"]

        flash(
            f"A plataforma está consultando o ano "
            f"{ano_letivo['ano']}.",
            "success"
        )

        return redirect("/anos-letivos")

    except sqlite3.Error as erro:

        print("ERRO AO SELECIONAR ANO LETIVO:", erro)

        flash(
            f"Erro ao selecionar o ano letivo: {erro}",
            "erro"
        )

        return redirect("/anos-letivos")

    finally:
        banco.close()


# =========================================================
# VOLTAR AO ANO ATIVO
# =========================================================

@app.route(
    "/anos-letivos/usar-ativo",
    methods=["POST"]
)
def usar_ano_letivo_ativo():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/acesso_negado")

    session.pop("ano_letivo_selecionado_id", None)
    session.pop("ano_letivo_visualizado", None)

    escola_id = obter_escola_usuario()

    if escola_id:
        atualizar_ano_letivo_na_sessao(escola_id)
    else:
        session.pop("ano_letivo_id", None)
        session.pop("ano_letivo", None)

    flash(
        "A plataforma voltou a utilizar o ano letivo ativo.",
        "success"
    )

    return redirect("/anos-letivos")


# =========================================================
# SALVAR AGENDAMENTO GLOBAL
# =========================================================

@app.route("/anos-letivos/agendamento-global", methods=["POST"])
def salvar_agendamento_global():

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    cursor = banco.cursor()

    try:
        ano = int(request.form.get("ano", "").strip())
        data_execucao = request.form.get("data_execucao", "").strip()
        data_inicio = request.form.get("data_inicio", "").strip() or None
        data_fim = request.form.get("data_fim", "").strip() or None

        if ano < 2000 or ano > 2100:
            raise ValueError("Informe um ano entre 2000 e 2100.")

        if not data_execucao:
            raise ValueError("Informe a data de execução.")

        if data_inicio and data_fim and data_fim < data_inicio:
            raise ValueError("A data final não pode ser anterior à inicial.")

        cursor.execute("""
            INSERT INTO configuracao_ano_letivo_global (
                id, ativo, ano, data_execucao, data_inicio, data_fim,
                copiar_turmas, copiar_vinculos, encerrar_anterior,
                executado, atualizado_em
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                ativo = excluded.ativo,
                ano = excluded.ano,
                data_execucao = excluded.data_execucao,
                data_inicio = excluded.data_inicio,
                data_fim = excluded.data_fim,
                copiar_turmas = excluded.copiar_turmas,
                copiar_vinculos = excluded.copiar_vinculos,
                encerrar_anterior = excluded.encerrar_anterior,
                executado = 0,
                atualizado_em = CURRENT_TIMESTAMP
        """, (
            1 if request.form.get("ativo") == "1" else 0,
            ano,
            data_execucao,
            data_inicio,
            data_fim,
            1 if request.form.get("copiar_turmas") == "1" else 0,
            1 if request.form.get("copiar_vinculos") == "1" else 0,
            1 if request.form.get("encerrar_anterior") == "1" else 0
        ))

        banco.commit()
        flash("Agendamento global salvo com sucesso.", "success")

    except (ValueError, sqlite3.Error) as erro:
        banco.rollback()
        flash(f"Erro ao salvar o agendamento global: {erro}", "erro")

    finally:
        banco.close()

    return redirect("/anos-letivos")


# =========================================================
# SALVAR CONFIGURAÇÃO DA INSTITUIÇÃO
# =========================================================

@app.route("/anos-letivos/agendamento-instituicao", methods=["POST"])
def salvar_agendamento_instituicao():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/acesso_negado")

    cargo = session.get("usuario_cargo", "").strip()

    if cargo == "Administrador Geral":
        escola_id = request.form.get("escola_id", "").strip()
    else:
        escola_id = obter_escola_usuario()

    try:
        escola_id = int(escola_id)
    except (TypeError, ValueError):
        flash("Selecione uma instituição válida.", "erro")
        return redirect("/anos-letivos")

    modo = request.form.get("modo", "global").strip().lower()

    if modo not in ("global", "proprio", "manual"):
        modo = "global"

    ano_texto = request.form.get("ano", "").strip()
    data_execucao = request.form.get("data_execucao", "").strip() or None
    data_inicio = request.form.get("data_inicio", "").strip() or None
    data_fim = request.form.get("data_fim", "").strip() or None

    try:
        ano = int(ano_texto) if ano_texto else None

        if ano is not None and (ano < 2000 or ano > 2100):
            raise ValueError("Informe um ano entre 2000 e 2100.")

        if modo == "proprio" and (ano is None or not data_execucao):
            raise ValueError(
                "No agendamento próprio, informe o ano e a data de execução."
            )

        if data_inicio and data_fim and data_fim < data_inicio:
            raise ValueError("A data final não pode ser anterior à inicial.")

        banco = conectar_banco()
        cursor = banco.cursor()

        cursor.execute("""
            INSERT INTO configuracao_ano_letivo_instituicao (
                escola_id, modo, ativo, ano, data_execucao,
                data_inicio, data_fim, copiar_turmas,
                copiar_vinculos, encerrar_anterior,
                executado, atualizado_em
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(escola_id) DO UPDATE SET
                modo = excluded.modo,
                ativo = excluded.ativo,
                ano = excluded.ano,
                data_execucao = excluded.data_execucao,
                data_inicio = excluded.data_inicio,
                data_fim = excluded.data_fim,
                copiar_turmas = excluded.copiar_turmas,
                copiar_vinculos = excluded.copiar_vinculos,
                encerrar_anterior = excluded.encerrar_anterior,
                executado = 0,
                atualizado_em = CURRENT_TIMESTAMP
        """, (
            escola_id,
            modo,
            1 if request.form.get("ativo") == "1" else 0,
            ano,
            data_execucao,
            data_inicio,
            data_fim,
            1 if request.form.get("copiar_turmas") == "1" else 0,
            1 if request.form.get("copiar_vinculos") == "1" else 0,
            1 if request.form.get("encerrar_anterior") == "1" else 0
        ))

        banco.commit()
        banco.close()

        flash("Configuração da instituição salva com sucesso.", "success")

    except (ValueError, sqlite3.Error) as erro:
        try:
            banco.rollback()
            banco.close()
        except Exception:
            pass
        flash(f"Erro ao salvar a configuração: {erro}", "erro")

    return redirect("/anos-letivos")


# =========================================================
# REABRIR ANO LETIVO
# =========================================================

@app.route(
    "/anos-letivos/<int:ano_letivo_id>/reabrir",
    methods=["POST"]
)
def reabrir_ano_letivo(ano_letivo_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição"
    ]):
        return redirect("/acesso_negado")

    cargo = session.get("usuario_cargo", "").strip()
    escola_usuario = obter_escola_usuario()
    modo = request.form.get("modo", "edicao").strip().lower()

    if modo not in ("edicao", "ativar"):
        modo = "edicao"

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        if cargo == "Administrador Geral":
            cursor.execute("""
                SELECT id, escola_id, ano
                FROM anos_letivos
                WHERE id = ?
                LIMIT 1
            """, (ano_letivo_id,))
        else:
            cursor.execute("""
                SELECT id, escola_id, ano
                FROM anos_letivos
                WHERE id = ?
                  AND escola_id = ?
                LIMIT 1
            """, (ano_letivo_id, escola_usuario))

        ano_letivo = cursor.fetchone()

        if not ano_letivo:
            flash("Ano letivo não encontrado ou sem permissão.", "erro")
            return redirect("/anos-letivos")

        if modo == "ativar":
            cursor.execute("""
                UPDATE anos_letivos
                SET ativo = 0,
                    encerrado = 1
                WHERE escola_id = ?
                  AND id <> ?
                  AND ativo = 1
            """, (ano_letivo["escola_id"], ano_letivo_id))

            cursor.execute("""
                UPDATE anos_letivos
                SET ativo = 1,
                    encerrado = 0
                WHERE id = ?
            """, (ano_letivo_id,))

            cursor.execute("""
                UPDATE escolas
                SET ano_letivo = ?
                WHERE id = ?
            """, (ano_letivo["ano"], ano_letivo["escola_id"]))

            acao = "REABRIR_E_ATIVAR"
            mensagem = (
                f"O ano letivo {ano_letivo['ano']} foi reaberto e ativado."
            )

            if cargo != "Administrador Geral":
                session["ano_letivo_selecionado_id"] = ano_letivo_id
                session["ano_letivo_id"] = ano_letivo_id
            else:
                session.pop("ano_letivo_id", None)
                session.pop("ano_letivo_selecionado_id", None)

            session["ano_letivo"] = ano_letivo["ano"]
            session["ano_letivo_visualizado"] = ano_letivo["ano"]

        else:
            cursor.execute("""
                UPDATE anos_letivos
                SET ativo = 0,
                    encerrado = 0
                WHERE id = ?
            """, (ano_letivo_id,))

            acao = "REABRIR_EDICAO"
            mensagem = (
                f"O ano letivo {ano_letivo['ano']} foi reaberto para edição."
            )

        cursor.execute("""
            INSERT INTO ano_letivo_auditoria (
                escola_id, ano_letivo_id, usuario_id, acao, detalhes
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            ano_letivo["escola_id"],
            ano_letivo_id,
            session.get("usuario_id"),
            acao,
            mensagem
        ))

        banco.commit()
        flash(mensagem, "success")

    except sqlite3.Error as erro:
        banco.rollback()
        flash(f"Erro ao reabrir o ano letivo: {erro}", "erro")

    finally:
        banco.close()

    return redirect("/anos-letivos")

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    )
