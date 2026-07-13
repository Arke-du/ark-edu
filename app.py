import base64
import json
import os
import sqlite3
import uuid
from datetime import datetime
from io import BytesIO

import cv2
import qrcode
from PIL import Image
from flask import Flask, flash, redirect, render_template, request, session
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
        CREATE TABLE IF NOT EXISTS questoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disciplina TEXT NOT NULL,
            enunciado TEXT NOT NULL,
            imagem TEXT,
            alternativa_a TEXT NOT NULL,
            alternativa_b TEXT NOT NULL,
            alternativa_c TEXT NOT NULL,
            alternativa_d TEXT NOT NULL,
            correta TEXT NOT NULL,
            habilidade TEXT,
            dificuldade TEXT NOT NULL,
            escola_id INTEGER,
            FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE
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
    garantir_coluna("provas", "professor_id", "INTEGER")
    garantir_coluna("provas", "data_geracao", "TEXT")
    garantir_coluna("provas", "data_aplicacao", "TEXT")
    garantir_coluna("provas", "escola_id", "INTEGER")
    garantir_coluna("provas", "ano_letivo_id", "INTEGER")
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

    banco.commit()
    banco.close()

# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def index():

    if "usuario_id" not in session:
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    usuario_id = session.get("usuario_id")
    usuario_cargo = session.get(
        "usuario_cargo",
        ""
    ).strip()

    escola_id = session.get("escola_id")

    total_instituicoes = 0
    total_usuarios = 0
    total_professores = 0
    total_alunos = 0
    total_turmas = 0
    total_questoes = 0
    total_provas = 0

    nome_instituicao = None

    ano_letivo_id_ativo = None
    ano_letivo_ativo = None

    permissoes_usuario = []

    try:

        # =====================================================
        # RECUPERAR A INSTITUIÇÃO DO USUÁRIO
        # =====================================================

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

            usuario_banco = cursor.fetchone()

            if (
                usuario_banco
                and usuario_banco["escola_id"]
            ):
                escola_id = usuario_banco["escola_id"]
                session["escola_id"] = escola_id

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
            """, (
                usuario_id,
            ))

            permissoes_usuario = [
                linha["modulo"]
                for linha in cursor.fetchall()
            ]

            # Administrador da Instituição sempre poderá
            # acessar a gestão de anos letivos.
            if (
                usuario_cargo == "Administrador da Instituição"
                and "Anos Letivos" not in permissoes_usuario
            ):
                permissoes_usuario.append("Anos Letivos")

        # =====================================================
        # ADMINISTRADOR GERAL
        #
        # Alunos, turmas e provas são contados somente nos
        # anos letivos ativos de cada instituição.
        # =====================================================

        if usuario_cargo == "Administrador Geral":

            # Instituições ativas
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM escolas
                WHERE COALESCE(status, 1) = 1
            """)

            resultado = cursor.fetchone()

            total_instituicoes = (
                resultado["total"]
                if resultado
                else 0
            )

            # Usuários ativos de todas as instituições
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM usuarios
                WHERE ativo = 1
            """)

            resultado = cursor.fetchone()

            total_usuarios = (
                resultado["total"]
                if resultado
                else 0
            )

            # Professores cadastrados
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM professores
            """)

            resultado = cursor.fetchone()

            total_professores = (
                resultado["total"]
                if resultado
                else 0
            )

            # Alunos dos anos ativos
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM alunos AS a

                INNER JOIN anos_letivos AS al
                    ON al.id = a.ano_letivo_id
                   AND al.escola_id = a.escola_id
                   AND al.ativo = 1
                   AND al.encerrado = 0
            """)

            resultado = cursor.fetchone()

            total_alunos = (
                resultado["total"]
                if resultado
                else 0
            )

            # Turmas dos anos ativos
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM turmas AS t

                INNER JOIN anos_letivos AS al
                    ON al.id = t.ano_letivo_id
                   AND al.escola_id = t.escola_id
                   AND al.ativo = 1
                   AND al.encerrado = 0
            """)

            resultado = cursor.fetchone()

            total_turmas = (
                resultado["total"]
                if resultado
                else 0
            )

            # Banco de questões não depende de ano letivo
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM questoes
            """)

            resultado = cursor.fetchone()

            total_questoes = (
                resultado["total"]
                if resultado
                else 0
            )

            # Provas dos anos ativos
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM provas AS p

                INNER JOIN anos_letivos AS al
                    ON al.id = p.ano_letivo_id
                   AND al.escola_id = p.escola_id
                   AND al.ativo = 1
                   AND al.encerrado = 0
            """)

            resultado = cursor.fetchone()

            total_provas = (
                resultado["total"]
                if resultado
                else 0
            )

        # =====================================================
        # USUÁRIOS VINCULADOS A UMA INSTITUIÇÃO
        # =====================================================

        elif escola_id:

            # =================================================
            # DADOS DA INSTITUIÇÃO
            # =================================================

            cursor.execute("""
                SELECT
                    id,
                    nome_instituicao
                FROM escolas
                WHERE id = ?
                LIMIT 1
            """, (
                escola_id,
            ))

            escola = cursor.fetchone()

            if escola:
                nome_instituicao = escola["nome_instituicao"]

            # =================================================
            # ANO LETIVO ATIVO DA INSTITUIÇÃO
            # =================================================

            cursor.execute("""
                SELECT
                    id,
                    ano
                FROM anos_letivos
                WHERE escola_id = ?
                  AND ativo = 1
                  AND encerrado = 0
                ORDER BY ano DESC
                LIMIT 1
            """, (
                escola_id,
            ))

            ano_ativo = cursor.fetchone()

            if ano_ativo:
                ano_letivo_id_ativo = ano_ativo["id"]
                ano_letivo_ativo = ano_ativo["ano"]

                # Guarda o ano ativo na sessão para ser
                # utilizado futuramente por outras páginas.
                session["ano_letivo_id"] = ano_letivo_id_ativo
                session["ano_letivo"] = ano_letivo_ativo

            else:
                session.pop("ano_letivo_id", None)
                session.pop("ano_letivo", None)

            # Instituição do usuário
            total_instituicoes = 1

            # =================================================
            # USUÁRIOS DA INSTITUIÇÃO
            # =================================================

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM usuarios
                WHERE escola_id = ?
                  AND ativo = 1
            """, (
                escola_id,
            ))

            resultado = cursor.fetchone()

            total_usuarios = (
                resultado["total"]
                if resultado
                else 0
            )

            # =================================================
            # PROFESSORES DA INSTITUIÇÃO
            # =================================================

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM professores
                WHERE escola_id = ?
            """, (
                escola_id,
            ))

            resultado = cursor.fetchone()

            total_professores = (
                resultado["total"]
                if resultado
                else 0
            )

            # =================================================
            # DADOS DO ANO LETIVO ATIVO
            # =================================================

            if ano_letivo_id_ativo:

                # Alunos do ano ativo
                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM alunos
                    WHERE escola_id = ?
                      AND ano_letivo_id = ?
                """, (
                    escola_id,
                    ano_letivo_id_ativo
                ))

                resultado = cursor.fetchone()

                total_alunos = (
                    resultado["total"]
                    if resultado
                    else 0
                )

                # Turmas do ano ativo
                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM turmas
                    WHERE escola_id = ?
                      AND ano_letivo_id = ?
                """, (
                    escola_id,
                    ano_letivo_id_ativo
                ))

                resultado = cursor.fetchone()

                total_turmas = (
                    resultado["total"]
                    if resultado
                    else 0
                )

                # Provas do ano ativo
                cursor.execute("""
                    SELECT COUNT(*) AS total
                    FROM provas
                    WHERE escola_id = ?
                      AND ano_letivo_id = ?
                """, (
                    escola_id,
                    ano_letivo_id_ativo
                ))

                resultado = cursor.fetchone()

                total_provas = (
                    resultado["total"]
                    if resultado
                    else 0
                )

            # =================================================
            # QUESTÕES DA INSTITUIÇÃO
            #
            # O banco de questões não é zerado na troca de ano.
            # =================================================

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM questoes
                WHERE escola_id = ?
            """, (
                escola_id,
            ))

            resultado = cursor.fetchone()

            total_questoes = (
                resultado["total"]
                if resultado
                else 0
            )

        # =====================================================
        # USUÁRIO SEM INSTITUIÇÃO
        # =====================================================

        else:

            nome_instituicao = (
                "Usuário sem instituição vinculada"
            )

        # =====================================================
        # CARREGAR O DASHBOARD
        # =====================================================

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

            permissoes_usuario=permissoes_usuario
        )

    except sqlite3.Error as erro:

        import traceback
        traceback.print_exc()

        print(
            "ERRO AO CARREGAR O DASHBOARD:",
            erro
        )

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

            ano_letivo_id_ativo=None,
            ano_letivo_ativo=None,

            permissoes_usuario=permissoes_usuario
        )

    finally:
        banco.close()

@app.route("/esqueci_senha")
def esqueci_senha():
    return render_template("esqueci_senha.html")

# =========================================================
# LISTAR TURMAS
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
    escola_id = session.get("escola_id")

    cargos_que_gerenciam = [
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]

    pode_gerenciar = cargo in cargos_que_gerenciam

    escolas = []
    lista_turmas = []
    ano_letivo_ativo = None

    try:

        # =====================================================
        # ADMINISTRADOR GERAL
        # Visualiza apenas o ano ativo de cada instituição
        # =====================================================

        if cargo == "Administrador Geral":

            cursor.execute("""
                SELECT
                    turmas.*,
                    escolas.nome_instituicao,

                    anos_letivos.id AS ano_letivo_id_atual,
                    anos_letivos.ano AS ano_letivo_atual,

                    (
                        SELECT COUNT(*)
                        FROM alunos
                        WHERE alunos.turma_id = turmas.id
                    ) AS total_alunos,

                    (
                        SELECT COUNT(DISTINCT pv.professor_id)
                        FROM professor_vinculos AS pv
                        WHERE pv.turma_id = turmas.id
                    ) AS total_professores

                FROM turmas

                INNER JOIN anos_letivos
                    ON anos_letivos.id = turmas.ano_letivo_id
                   AND anos_letivos.escola_id = turmas.escola_id
                   AND anos_letivos.ativo = 1

                LEFT JOIN escolas
                    ON escolas.id = turmas.escola_id

                ORDER BY
                    escolas.nome_instituicao COLLATE NOCASE ASC,
                    anos_letivos.ano DESC,
                    turmas.etapa COLLATE NOCASE ASC,
                    turmas.ano COLLATE NOCASE ASC,
                    turmas.nome COLLATE NOCASE ASC,
                    turmas.turno COLLATE NOCASE ASC
            """)

            lista_turmas = cursor.fetchall()

            cursor.execute("""
                SELECT
                    escolas.id,
                    escolas.nome_instituicao,
                    anos_letivos.id AS ano_letivo_id,
                    anos_letivos.ano AS ano_letivo_ativo

                FROM escolas

                LEFT JOIN anos_letivos
                    ON anos_letivos.escola_id = escolas.id
                   AND anos_letivos.ativo = 1

                WHERE COALESCE(escolas.status, 1) = 1

                ORDER BY
                    escolas.nome_instituicao COLLATE NOCASE ASC
            """)

            escolas = cursor.fetchall()

        # =====================================================
        # PROFESSOR
        # Somente turmas em que possui vínculo no ano ativo
        # =====================================================

        elif cargo == "Professor":

            if not escola_id and usuario_id:

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

            cursor.execute("""
                SELECT
                    ano,
                    id
                FROM anos_letivos
                WHERE escola_id = ?
                  AND ativo = 1
                LIMIT 1
            """, (
                escola_id,
            ))

            ano_ativo = cursor.fetchone()

            if ano_ativo:
                ano_letivo_ativo = ano_ativo["ano"]

            cursor.execute("""
                SELECT DISTINCT
                    turmas.*,
                    escolas.nome_instituicao,

                    anos_letivos.id AS ano_letivo_id_atual,
                    anos_letivos.ano AS ano_letivo_atual,

                    (
                        SELECT COUNT(*)
                        FROM alunos
                        WHERE alunos.turma_id = turmas.id
                    ) AS total_alunos,

                    (
                        SELECT COUNT(DISTINCT pv_total.professor_id)
                        FROM professor_vinculos AS pv_total
                        WHERE pv_total.turma_id = turmas.id
                    ) AS total_professores

                FROM turmas

                INNER JOIN professor_vinculos AS pv
                    ON pv.turma_id = turmas.id

                INNER JOIN anos_letivos
                    ON anos_letivos.id = turmas.ano_letivo_id
                   AND anos_letivos.escola_id = turmas.escola_id
                   AND anos_letivos.ativo = 1

                LEFT JOIN escolas
                    ON escolas.id = turmas.escola_id

                WHERE pv.professor_id = ?
                  AND turmas.escola_id = ?

                ORDER BY
                    turmas.etapa COLLATE NOCASE ASC,
                    turmas.ano COLLATE NOCASE ASC,
                    turmas.nome COLLATE NOCASE ASC,
                    turmas.turno COLLATE NOCASE ASC
            """, (
                usuario_id,
                escola_id
            ))

            lista_turmas = cursor.fetchall()

        # =====================================================
        # ADMINISTRADOR DA INSTITUIÇÃO,
        # COORDENADOR E SECRETARIA
        # =====================================================

        else:

            # Recupera a instituição diretamente do usuário,
            # caso ela não esteja registrada na sessão.
            if not escola_id and usuario_id:

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

            # Busca o ano ativo da instituição.
            cursor.execute("""
                SELECT
                    id,
                    ano
                FROM anos_letivos
                WHERE escola_id = ?
                  AND ativo = 1
                LIMIT 1
            """, (
                escola_id,
            ))

            ano_ativo = cursor.fetchone()

            if ano_ativo:
                ano_letivo_ativo = ano_ativo["ano"]

            cursor.execute("""
                SELECT
                    turmas.*,
                    escolas.nome_instituicao,

                    anos_letivos.id AS ano_letivo_id_atual,
                    anos_letivos.ano AS ano_letivo_atual,

                    (
                        SELECT COUNT(*)
                        FROM alunos
                        WHERE alunos.turma_id = turmas.id
                    ) AS total_alunos,

                    (
                        SELECT COUNT(DISTINCT pv.professor_id)
                        FROM professor_vinculos AS pv
                        WHERE pv.turma_id = turmas.id
                    ) AS total_professores

                FROM turmas

                INNER JOIN anos_letivos
                    ON anos_letivos.id = turmas.ano_letivo_id
                   AND anos_letivos.escola_id = turmas.escola_id
                   AND anos_letivos.ativo = 1

                LEFT JOIN escolas
                    ON escolas.id = turmas.escola_id

                WHERE turmas.escola_id = ?

                ORDER BY
                    turmas.etapa COLLATE NOCASE ASC,
                    turmas.ano COLLATE NOCASE ASC,
                    turmas.nome COLLATE NOCASE ASC,
                    turmas.turno COLLATE NOCASE ASC
            """, (
                escola_id,
            ))

            lista_turmas = cursor.fetchall()

        return render_template(
            "gestao/turmas.html",
            turmas=lista_turmas,
            escolas=escolas,
            cargo=cargo,
            pode_gerenciar=pode_gerenciar,
            ano_letivo_ativo=ano_letivo_ativo
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
            ano_letivo_ativo=None
        )

    finally:
        banco.close()


# =========================================================
# CADASTRAR TURMA
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
    ano = request.form.get("ano", "").strip()
    identificacao = request.form.get("nome", "").strip()
    turno = request.form.get("turno", "").strip()

    usuario_id = session.get("usuario_id")
    cargo = session.get("usuario_cargo", "").strip()
    escola_id = session.get("escola_id")

    if not etapa:
        flash("Selecione a etapa de ensino.", "erro")
        return redirect("/turmas")

    if not ano:
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

        if (
            cargo != "Administrador Geral"
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

        # Administrador Geral seleciona a instituição.
        if cargo == "Administrador Geral":

            escola_formulario = request.form.get(
                "escola_id",
                ""
            ).strip()

            if escola_formulario:
                escola_id = escola_formulario

        if not escola_id:

            flash(
                "Não foi possível identificar a instituição da turma.",
                "erro"
            )

            return redirect("/turmas")

        try:
            escola_id = int(escola_id)

        except (TypeError, ValueError):

            flash(
                "A instituição selecionada é inválida.",
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
        # BUSCAR ANO LETIVO ATIVO DA INSTITUIÇÃO
        # =====================================================

        cursor.execute("""
            SELECT
                id,
                ano
            FROM anos_letivos
            WHERE escola_id = ?
              AND ativo = 1
              AND encerrado = 0
            LIMIT 1
        """, (
            escola_id,
        ))

        ano_letivo = cursor.fetchone()

        if ano_letivo is None:

            flash(
                "A instituição não possui um ano letivo ativo. "
                "Abra um ano letivo antes de cadastrar a turma.",
                "erro"
            )

            return redirect("/turmas")

        ano_letivo_id = ano_letivo["id"]
        numero_ano_letivo = ano_letivo["ano"]

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
            ano,
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
        #
        # ano_letivo:
        # campo antigo mantido temporariamente por compatibilidade
        #
        # ano_letivo_id:
        # novo vínculo oficial com a tabela anos_letivos
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
            ano,
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
# LISTAR ALUNOS
# =========================================================

@app.route("/alunos")
def alunos():

    if not permissao_modulo("Alunos"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    usuario_id = session.get("usuario_id")
    cargo = session.get("usuario_cargo", "").strip()
    escola_id = session.get("escola_id")

    lista_turmas = []
    lista_alunos = []

    ano_letivo_id_ativo = None
    ano_letivo_ativo = None

    try:

        # =====================================================
        # RECUPERAR A INSTITUIÇÃO DO USUÁRIO
        # =====================================================

        if (
            cargo != "Administrador Geral"
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

        # =====================================================
        # ADMINISTRADOR GERAL
        #
        # Visualiza alunos dos anos ativos de todas as escolas.
        # =====================================================

        if cargo == "Administrador Geral":

            cursor.execute("""
                SELECT
                    t.id,
                    t.nome,
                    t.etapa,
                    t.ano,
                    t.turno,
                    t.escola_id,
                    t.ano_letivo_id,

                    e.nome_instituicao,

                    al.ano AS ano_letivo

                FROM turmas AS t

                INNER JOIN anos_letivos AS al
                    ON al.id = t.ano_letivo_id
                   AND al.escola_id = t.escola_id
                   AND al.ativo = 1
                   AND al.encerrado = 0

                INNER JOIN escolas AS e
                    ON e.id = t.escola_id

                ORDER BY
                    e.nome_instituicao COLLATE NOCASE ASC,
                    t.etapa COLLATE NOCASE ASC,
                    t.ano COLLATE NOCASE ASC,
                    t.nome COLLATE NOCASE ASC,
                    t.turno COLLATE NOCASE ASC
            """)

            lista_turmas = cursor.fetchall()

            cursor.execute("""
                SELECT
                    a.id,
                    a.nome,
                    a.matricula,
                    a.turma_id,
                    a.escola_id,
                    a.ano_letivo_id,

                    t.nome AS nome_turma,
                    t.ano AS ano_turma,
                    t.etapa,
                    t.turno,

                    e.nome_instituicao,

                    al.ano AS ano_letivo

                FROM alunos AS a

                INNER JOIN turmas AS t
                    ON t.id = a.turma_id
                   AND t.escola_id = a.escola_id
                   AND t.ano_letivo_id = a.ano_letivo_id

                INNER JOIN anos_letivos AS al
                    ON al.id = a.ano_letivo_id
                   AND al.escola_id = a.escola_id
                   AND al.ativo = 1
                   AND al.encerrado = 0

                INNER JOIN escolas AS e
                    ON e.id = a.escola_id

                ORDER BY
                    e.nome_instituicao COLLATE NOCASE ASC,
                    a.nome COLLATE NOCASE ASC
            """)

            lista_alunos = cursor.fetchall()

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
                    "alunos.html",
                    alunos=[],
                    turmas=[],
                    ano_letivo_ativo=None
                )

            # =================================================
            # BUSCAR O ANO LETIVO ATIVO
            # =================================================

            cursor.execute("""
                SELECT
                    id,
                    ano
                FROM anos_letivos
                WHERE escola_id = ?
                  AND ativo = 1
                  AND encerrado = 0
                ORDER BY ano DESC
                LIMIT 1
            """, (
                escola_id,
            ))

            ano_ativo = cursor.fetchone()

            if ano_ativo:

                ano_letivo_id_ativo = ano_ativo["id"]
                ano_letivo_ativo = ano_ativo["ano"]

                session["ano_letivo_id"] = ano_letivo_id_ativo
                session["ano_letivo"] = ano_letivo_ativo

            else:

                session.pop("ano_letivo_id", None)
                session.pop("ano_letivo", None)

                flash(
                    "A instituição não possui um ano letivo ativo.",
                    "erro"
                )

                return render_template(
                    "alunos.html",
                    alunos=[],
                    turmas=[],
                    ano_letivo_ativo=None
                )

            # =================================================
            # TURMAS DO ANO ATIVO
            # =================================================

            cursor.execute("""
                SELECT
                    t.id,
                    t.nome,
                    t.etapa,
                    t.ano,
                    t.turno,
                    t.escola_id,
                    t.ano_letivo_id,

                    al.ano AS ano_letivo

                FROM turmas AS t

                INNER JOIN anos_letivos AS al
                    ON al.id = t.ano_letivo_id
                   AND al.escola_id = t.escola_id

                WHERE t.escola_id = ?
                  AND t.ano_letivo_id = ?

                ORDER BY
                    t.etapa COLLATE NOCASE ASC,
                    t.ano COLLATE NOCASE ASC,
                    t.nome COLLATE NOCASE ASC,
                    t.turno COLLATE NOCASE ASC
            """, (
                escola_id,
                ano_letivo_id_ativo
            ))

            lista_turmas = cursor.fetchall()

            # =================================================
            # ALUNOS DO ANO ATIVO
            # =================================================

            cursor.execute("""
                SELECT
                    a.id,
                    a.nome,
                    a.matricula,
                    a.turma_id,
                    a.escola_id,
                    a.ano_letivo_id,

                    t.nome AS nome_turma,
                    t.ano AS ano_turma,
                    t.etapa,
                    t.turno,

                    al.ano AS ano_letivo

                FROM alunos AS a

                INNER JOIN turmas AS t
                    ON t.id = a.turma_id
                   AND t.escola_id = a.escola_id
                   AND t.ano_letivo_id = a.ano_letivo_id

                INNER JOIN anos_letivos AS al
                    ON al.id = a.ano_letivo_id
                   AND al.escola_id = a.escola_id

                WHERE a.escola_id = ?
                  AND a.ano_letivo_id = ?

                ORDER BY
                    a.nome COLLATE NOCASE ASC
            """, (
                escola_id,
                ano_letivo_id_ativo
            ))

            lista_alunos = cursor.fetchall()

        return render_template(
            "alunos.html",
            alunos=lista_alunos,
            turmas=lista_turmas,
            ano_letivo_ativo=ano_letivo_ativo,
            cargo=cargo
        )

    except sqlite3.Error as erro:

        import traceback
        traceback.print_exc()

        print(
            "ERRO AO LISTAR ALUNOS:",
            erro
        )

        flash(
            f"Erro ao carregar os alunos: {erro}",
            "erro"
        )

        return render_template(
            "alunos.html",
            alunos=[],
            turmas=[],
            ano_letivo_ativo=None,
            cargo=cargo
        )

    finally:
        banco.close()

# =========================================================
# CADASTRAR ALUNO
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
    matricula = request.form.get("matricula", "").strip()
    turma_id = request.form.get("turma_id", "").strip()

    usuario_id = session.get("usuario_id")
    cargo = session.get("usuario_cargo", "").strip()
    escola_id = session.get("escola_id")

    if not nome:
        flash("Informe o nome do aluno.", "erro")
        return redirect("/alunos")

    if not matricula:
        flash("Informe a matrícula.", "erro")
        return redirect("/alunos")

    if not turma_id:
        flash("Selecione uma turma.", "erro")
        return redirect("/alunos")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        # =====================================================
        # RECUPERAR ESCOLA DO USUÁRIO
        # =====================================================

        if (
            cargo != "Administrador Geral"
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

            if usuario:
                escola_id = usuario["escola_id"]
                session["escola_id"] = escola_id

        if not escola_id:

            flash(
                "Não foi possível identificar a instituição.",
                "erro"
            )

            return redirect("/alunos")

        # =====================================================
        # BUSCAR A TURMA
        # =====================================================

        cursor.execute("""
            SELECT
                id,
                escola_id,
                ano_letivo_id
            FROM turmas
            WHERE id = ?
            LIMIT 1
        """, (
            turma_id,
        ))

        turma = cursor.fetchone()

        if turma is None:

            flash(
                "A turma selecionada não existe.",
                "erro"
            )

            return redirect("/alunos")

        # Segurança
        if cargo != "Administrador Geral":

            if turma["escola_id"] != escola_id:

                flash(
                    "A turma não pertence à sua instituição.",
                    "erro"
                )

                return redirect("/alunos")

        ano_letivo_id = turma["ano_letivo_id"]

        # =====================================================
        # VERIFICAR MATRÍCULA DUPLICADA
        # =====================================================

        cursor.execute("""
            SELECT id
            FROM alunos
            WHERE matricula = ?
              AND escola_id = ?
              AND ano_letivo_id = ?
            LIMIT 1
        """, (
            matricula,
            escola_id,
            ano_letivo_id
        ))

        if cursor.fetchone():

            flash(
                "Já existe um aluno com essa matrícula neste ano letivo.",
                "erro"
            )

            return redirect("/alunos")

        # =====================================================
        # CADASTRAR ALUNO
        # =====================================================

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

        banco.commit()

        flash(
            "Aluno cadastrado com sucesso.",
            "success"
        )

        return redirect("/alunos")

    except sqlite3.Error as erro:

        banco.rollback()

        import traceback
        traceback.print_exc()

        print(
            "ERRO AO CADASTRAR ALUNO:",
            erro
        )

        flash(
            f"Erro ao cadastrar aluno: {erro}",
            "erro"
        )

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
    cursor = banco.cursor()

    cursor.execute("""
        SELECT *
        FROM questoes
        ORDER BY id DESC
    """)
    lista_questoes = cursor.fetchall()

    banco.close()

    return render_template(
        "questoes.html",
        questoes=lista_questoes
    )

@app.route("/cadastrar_questao", methods=["POST"])
def cadastrar_questao():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    ]):
        return redirect("/login")

    disciplina = request.form["disciplina"]
    enunciado = request.form["enunciado"]

    imagem = request.files.get("imagem")
    nome_imagem = ""

    if imagem and imagem.filename != "":
        nome_imagem = secure_filename(imagem.filename)
        imagem.save(
            os.path.join(
                app.config["UPLOAD_FOLDER"],
                nome_imagem
            )
        )

    alternativa_a = request.form["alternativa_a"]
    alternativa_b = request.form["alternativa_b"]
    alternativa_c = request.form["alternativa_c"]
    alternativa_d = request.form["alternativa_d"]
    correta = request.form["correta"]
    habilidade = request.form["habilidade"]
    dificuldade = request.form["dificuldade"]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        INSERT INTO questoes (
            disciplina,
            enunciado,
            imagem,
            alternativa_a,
            alternativa_b,
            alternativa_c,
            alternativa_d,
            correta,
            habilidade,
            dificuldade
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        disciplina,
        enunciado,
        nome_imagem,
        alternativa_a,
        alternativa_b,
        alternativa_c,
        alternativa_d,
        correta,
        habilidade,
        dificuldade
    ))

    banco.commit()
    banco.close()

    return redirect("/questoes")

@app.route("/provas")
def provas():

    if not permissao_modulo("Provas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT *
        FROM turmas
        ORDER BY nome
    """)
    lista_turmas = cursor.fetchall()

    cursor.execute("""
        SELECT *
        FROM professores
        ORDER BY nome
    """)
    lista_professores = cursor.fetchall()

    cursor.execute("""
        SELECT
            provas.id,
            provas.nome,
            turmas.nome,
            professores.nome,
            provas.disciplina,
            provas.quantidade,
            provas.data_geracao,
            provas.data_aplicacao
        FROM provas
        JOIN turmas
            ON provas.turma_id = turmas.id
        LEFT JOIN professores
            ON provas.professor_id = professores.id
        ORDER BY provas.id DESC
    """)
    lista_provas = cursor.fetchall()

    banco.close()

    return render_template(
        "provas.html",
        turmas=lista_turmas,
        professores=lista_professores,
        provas=lista_provas
    )

@app.route("/gerar_prova", methods=["POST"])
def gerar_prova():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    ]):
        return redirect("/login")

    nome = request.form["nome"]
    turma_id = request.form["turma_id"]
    professor_id = request.form["professor_id"]
    disciplina = request.form["disciplina"]
    quantidade = int(request.form["quantidade"])
    data_aplicacao = request.form["data_aplicacao"]
    data_geracao = datetime.now().strftime("%d/%m/%Y")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM questoes
        WHERE disciplina = ?
    """, (disciplina,))

    total_questoes = cursor.fetchone()[0]

    if total_questoes < quantidade:
        banco.close()
        return f"""
        <h2>Quantidade insuficiente de questões.</h2>

        <p>Existem apenas <strong>{total_questoes}</strong>
        questões cadastradas para a disciplina
        <strong>{disciplina}</strong>.</p>

        <a href="/provas">Voltar</a>
        """

    cursor.execute("""
        INSERT INTO provas
        (
            nome,
            turma_id,
            professor_id,
            disciplina,
            quantidade,
            data_geracao,
            data_aplicacao
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        nome,
        turma_id,
        professor_id,
        disciplina,
        quantidade,
        data_geracao,
        data_aplicacao
    ))

    prova_id = cursor.lastrowid

    cursor.execute("""
        SELECT id
        FROM questoes
        WHERE disciplina = ?
        ORDER BY RANDOM()
        LIMIT ?
    """, (disciplina, quantidade))

    questoes_selecionadas = cursor.fetchall()

    for questao in questoes_selecionadas:
        cursor.execute("""
            INSERT INTO prova_questoes (
                prova_id,
                questao_id
            )
            VALUES (?, ?)
        """, (
            prova_id,
            questao[0]
        ))

    banco.commit()
    banco.close()

    return redirect("/provas")

@app.route("/prova/<int:prova_id>")
def visualizar_prova(prova_id):

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
            provas.id,
            provas.nome,
            turmas.nome,
            provas.disciplina,
            provas.quantidade,
            professores.nome,
            provas.data_geracao,
            provas.data_aplicacao
        FROM provas
        JOIN turmas
            ON provas.turma_id = turmas.id
        LEFT JOIN professores
            ON provas.professor_id = professores.id
        WHERE provas.id = ?
    """, (prova_id,))

    prova = cursor.fetchone()

    cursor.execute("""
        SELECT *
        FROM instituicao
        WHERE id = 1
    """)
    instituicao = cursor.fetchone()

    cursor.execute("""
        SELECT questoes.*
        FROM prova_questoes
        JOIN questoes
            ON prova_questoes.questao_id = questoes.id
        WHERE prova_questoes.prova_id = ?
        ORDER BY prova_questoes.id
    """, (prova_id,))

    questoes = cursor.fetchall()

    banco.close()

    return render_template(
        "visualizar_prova.html",
        prova=prova,
        questoes=questoes,
        instituicao=instituicao
    )

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

@app.route("/excluir_prova/<int:prova_id>")
def excluir_prova(prova_id):

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Professor"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "DELETE FROM prova_questoes WHERE prova_id = ?",
        (prova_id,)
    )

    cursor.execute(
        "DELETE FROM provas WHERE id = ?",
        (prova_id,)
    )

    banco.commit()
    banco.close()

    return redirect("/provas")

@app.route("/editar_prova/<int:prova_id>")
def editar_prova(prova_id):
    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("SELECT * FROM provas WHERE id = ?", (prova_id,))
    prova = cursor.fetchone()

    cursor.execute("SELECT * FROM turmas")
    turmas = cursor.fetchall()

    cursor.execute("SELECT * FROM professores")
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
    nome = request.form["nome"]
    turma_id = request.form["turma_id"]
    professor_id = request.form["professor_id"]
    disciplina = request.form["disciplina"]
    quantidade = request.form["quantidade"]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        UPDATE provas
        SET nome = ?, turma_id = ?, professor_id = ?, disciplina = ?, quantidade = ?
        WHERE id = ?
    """, (nome, turma_id, professor_id, disciplina, quantidade, prova_id))

    banco.commit()
    banco.close()

    return redirect("/provas")

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
        ORDER BY prova_questoes.id
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
    cursor = banco.cursor()

    cursor.execute("""
        SELECT nome
        FROM provas
        WHERE id = ?
    """, (prova_id,))
    prova = cursor.fetchone()

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
    resultados = cursor.fetchall()

    cursor.execute("""
        SELECT
            COUNT(*),
            ROUND(AVG(nota), 1),
            MAX(nota),
            MIN(nota)
        FROM resultados
        WHERE prova_id = ?
    """, (prova_id,))

    estatisticas = cursor.fetchone()

    total_alunos = estatisticas[0] or 0
    media_turma = estatisticas[1] or 0
    maior_nota = estatisticas[2] or 0
    menor_nota = estatisticas[3] or 0

    cursor.execute("""
        SELECT COUNT(*)
        FROM resultados
        WHERE prova_id = ?
        AND nota >= 6
    """, (prova_id,))
    aprovados = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM resultados
        WHERE prova_id = ?
        AND nota < 6
    """, (prova_id,))
    reprovados = cursor.fetchone()[0]

    taxa_aprovacao = 0

    if total_alunos > 0:
        taxa_aprovacao = round(
            (aprovados / total_alunos) * 100,
            1
        )

    banco.close()

    return render_template(
        "resultados.html",
        prova=prova,
        resultados=resultados,
        total_alunos=total_alunos,
        media_turma=media_turma,
        maior_nota=maior_nota,
        menor_nota=menor_nota,
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
        ORDER BY prova_questoes.id
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
                SELECT id
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
                SELECT id
                FROM componentes_curriculares
                WHERE id = ?
                  AND escola_id = ?
                  AND ativo = 1
                LIMIT 1
            """, (
                componente_id,
                escola_professor_id
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

            # Verifica vínculo duplicado
            cursor.execute("""
                SELECT id
                FROM professor_vinculos
                WHERE professor_id = ?
                  AND turma_id = ?
                  AND componente_id = ?
                LIMIT 1
            """, (
                professor_id,
                turma_id,
                componente_id
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
        # LISTA OS COMPONENTES DA INSTITUIÇÃO
        # =================================================

        cursor.execute("""
            SELECT
                id,
                nome
            FROM componentes_curriculares
            WHERE escola_id = ?
              AND ativo = 1
            ORDER BY nome COLLATE NOCASE ASC
        """, (
            escola_professor_id,
        ))

        componentes = cursor.fetchall()

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

    try:

        if cargo == "Administrador Geral":

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

                    (
                        SELECT COUNT(*)
                        FROM turmas AS t
                        WHERE t.ano_letivo_id = al.id
                    ) AS total_turmas,

                    (
                        SELECT COUNT(*)
                        FROM alunos AS a
                        WHERE a.ano_letivo_id = al.id
                    ) AS total_alunos,

                    (
                        SELECT COUNT(*)
                        FROM provas AS p
                        WHERE p.ano_letivo_id = al.id
                    ) AS total_provas

                FROM anos_letivos AS al

                INNER JOIN escolas AS e
                    ON e.id = al.escola_id

                ORDER BY
                    e.nome_instituicao COLLATE NOCASE ASC,
                    al.ano DESC
            """)

            lista_anos = cursor.fetchall()

            cursor.execute("""
                SELECT
                    id,
                    nome_instituicao
                FROM escolas
                WHERE COALESCE(status, 1) = 1
                ORDER BY nome_instituicao COLLATE NOCASE ASC
            """)

            escolas = cursor.fetchall()

        else:

            if not escola_id:
                flash(
                    "Não foi possível identificar sua instituição.",
                    "erro"
                )
                return redirect("/")

            atualizar_ano_letivo_na_sessao(escola_id)

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

                    (
                        SELECT COUNT(*)
                        FROM turmas AS t
                        WHERE t.ano_letivo_id = al.id
                    ) AS total_turmas,

                    (
                        SELECT COUNT(*)
                        FROM alunos AS a
                        WHERE a.ano_letivo_id = al.id
                    ) AS total_alunos,

                    (
                        SELECT COUNT(*)
                        FROM provas AS p
                        WHERE p.ano_letivo_id = al.id
                    ) AS total_provas

                FROM anos_letivos AS al

                INNER JOIN escolas AS e
                    ON e.id = al.escola_id

                WHERE al.escola_id = ?

                ORDER BY al.ano DESC
            """, (
                escola_id,
            ))

            lista_anos = cursor.fetchall()

        return render_template(
            "gestao/anos_letivos.html",
            anos_letivos=lista_anos,
            escolas=escolas,
            cargo=cargo
        )

    except sqlite3.Error as erro:

        import traceback
        traceback.print_exc()

        print("ERRO AO LISTAR ANOS LETIVOS:", erro)

        flash(
            f"Erro ao carregar os anos letivos: {erro}",
            "erro"
        )

        return render_template(
            "gestao/anos_letivos.html",
            anos_letivos=[],
            escolas=[],
            cargo=cargo
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

    copiar_turmas = (
        request.form.get("copiar_turmas") == "1"
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

        if copiar_turmas and ano_anterior:

            cursor.execute("""
                SELECT
                    nome,
                    etapa,
                    ano,
                    turno
                FROM turmas
                WHERE escola_id = ?
                  AND ano_letivo_id = ?
                ORDER BY id
            """, (
                escola_id,
                ano_anterior["id"]
            ))

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

                total_turmas_copiadas += 1

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

        mensagem = f"Ano letivo {ano} aberto com sucesso."

        if copiar_turmas:
            mensagem += (
                f" {total_turmas_copiadas} turma(s) "
                "foram copiadas."
            )

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

        session["ano_letivo_selecionado_id"] = ano_letivo["id"]
        session["ano_letivo_id"] = ano_letivo["id"]
        session["ano_letivo"] = ano_letivo["ano"]

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

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    )
