from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash
from flask import Flask, render_template, request, redirect, session, flash
import sqlite3
import os
import qrcode
import base64
from io import BytesIO
from datetime import datetime
from werkzeug.utils import secure_filename
# import cv2
# from pyzbar.pyzbar import decode
from PIL import Image
# import numpy as np
import cv2

app = Flask(__name__)
app.secret_key = "chave_secreta_plataforma_avaliacao"

app.config["MAIL_SERVER"] = "smtp.gmail.com"
app.config["MAIL_PORT"] = 587
app.config["MAIL_USE_TLS"] = True
app.config["MAIL_USERNAME"] = "arkedu.plataforma@gmail.com"
app.config["MAIL_PASSWORD"] = "fhvc wvqq itom pgee"
app.config["MAIL_DEFAULT_SENDER"] = "arkedu.plataforma@gmail.com"

mail = Mail(app)

serializer = URLSafeTimedSerializer(app.secret_key)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "plataforma.db")

UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def conectar_banco():
    return sqlite3.connect(DB_PATH)

def cargo_permitido(cargos_permitidos):

    if "usuario_id" not in session:
        return False

    return session.get("usuario_cargo") in cargos_permitidos

def criar_tabelas():
    banco = conectar_banco()
    cursor = banco.cursor()

    # ==========================
    # TABELA ESCOLAS
    # ==========================

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

        ano_letivo TEXT,

        modalidade_ensino TEXT,

        etapas_ensino TEXT,

        logo TEXT,

        status INTEGER DEFAULT 1,

        criado_em TEXT
    )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            ano TEXT NOT NULL,
            turno TEXT NOT NULL
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
            acertou INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alunos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            matricula TEXT,
            turma_id INTEGER NOT NULL,
            FOREIGN KEY (turma_id) REFERENCES turmas(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT,
            disciplina TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professor_disciplinas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id INTEGER NOT NULL,
            disciplina TEXT NOT NULL,
            FOREIGN KEY (professor_id) REFERENCES professores(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS professor_turmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id INTEGER NOT NULL,
            turma_id INTEGER NOT NULL,
            FOREIGN KEY (professor_id) REFERENCES professores(id),
            FOREIGN KEY (turma_id) REFERENCES turmas(id)
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
            dificuldade TEXT NOT NULL
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
            FOREIGN KEY (turma_id) REFERENCES turmas(id),
            FOREIGN KEY (professor_id) REFERENCES professores(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prova_questoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prova_id INTEGER NOT NULL,
            questao_id INTEGER NOT NULL,
            FOREIGN KEY (prova_id) REFERENCES provas(id),
            FOREIGN KEY (questao_id) REFERENCES questoes(id)
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
        CREATE TABLE IF NOT EXISTS resultados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prova_id INTEGER,
            aluno_id INTEGER,
            acertos INTEGER,
            erros INTEGER,
            nota REAL
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
            acertou INTEGER
        )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cargos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT UNIQUE
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
            FOREIGN KEY (cargo_id) REFERENCES cargos(id)
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
            FOREIGN KEY (cargo_id) REFERENCES cargos(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS permissoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cargo_id INTEGER NOT NULL,
            modulo TEXT NOT NULL,
            pode_acessar INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS codigos_recuperacao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            codigo TEXT NOT NULL,
            usado INTEGER DEFAULT 0,
            criado_em TEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    """)

    cargos = [
        "Administrador",
        "Coordenador",
        "Professor",
        "Secretaria"
    ]

    for cargo in cargos:
        try:
            cursor.execute(
                "INSERT INTO cargos (nome) VALUES (?)",
                (cargo,)
            )
        except:
            pass

    try:
        cursor.execute("""
            INSERT INTO usuarios
            (nome, email, senha, cargo_id)
            VALUES (?, ?, ?, ?)
        """, (
            "Administrador",
            "admin",
            "admin123",
            1
            ))
    except:
            pass

    try:
        cursor.execute(
            "ALTER TABLE provas ADD COLUMN professor_id INTEGER"
        )
    except sqlite3.OperationalError:
        pass


    try:
        cursor.execute(
            "ALTER TABLE provas ADD COLUMN data_aplicacao TEXT"
        )
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE instituicao ADD COLUMN logo TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE provas ADD COLUMN data_geracao TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE provas ADD COLUMN data_aplicacao TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE permissoes ADD COLUMN pode_acessar INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    banco.commit()
    banco.close()

@app.route("/")
def index():

    if "usuario_id" not in session:
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    usuario_id = session.get("usuario_id")
    usuario_cargo = session.get("usuario_cargo", "").strip()
    escola_id = session.get("escola_id")

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

        # ==========================================
        # RECUPERA A INSTITUIÇÃO DO USUÁRIO
        # ==========================================

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
            """, (usuario_id,))

            usuario_banco = cursor.fetchone()

            if usuario_banco and usuario_banco["escola_id"]:
                escola_id = usuario_banco["escola_id"]
                session["escola_id"] = escola_id

        # ==========================================
        # PERMISSÕES DO USUÁRIO
        # ==========================================

        if usuario_cargo == "Administrador Geral":

            permissoes_usuario = [
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

        # ==========================================
        # ADMINISTRADOR GERAL
        # ==========================================

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
                FROM alunos
            """)
            total_alunos = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM turmas
            """)
            total_turmas = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM questoes
            """)
            total_questoes = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM provas
            """)
            total_provas = cursor.fetchone()["total"]

        # ==========================================
        # USUÁRIOS VINCULADOS A UMA INSTITUIÇÃO
        # ==========================================

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

            # Instituição do usuário
            total_instituicoes = 1

            # Usuários da instituição
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM usuarios
                WHERE escola_id = ?
                  AND ativo = 1
            """, (escola_id,))

            total_usuarios = cursor.fetchone()["total"]

            # Professores da instituição
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM professores
                WHERE escola_id = ?
            """, (escola_id,))

            total_professores = cursor.fetchone()["total"]

            # Alunos da instituição
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM alunos
                WHERE escola_id = ?
            """, (escola_id,))

            total_alunos = cursor.fetchone()["total"]

            # Turmas da instituição
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM turmas
                WHERE escola_id = ?
            """, (escola_id,))

            total_turmas = cursor.fetchone()["total"]

            # Questões da instituição
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM questoes
                WHERE escola_id = ?
            """, (escola_id,))

            total_questoes = cursor.fetchone()["total"]

            # Provas da instituição
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM provas
                WHERE escola_id = ?
            """, (escola_id,))

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
            permissoes_usuario=permissoes_usuario
        )

    except sqlite3.OperationalError as erro:

        print(f"Erro no dashboard: {erro}")

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
            permissoes_usuario=permissoes_usuario
        )

    finally:
        banco.close()

@app.route("/esqueci_senha")
def esqueci_senha():
    return render_template("esqueci_senha.html")

@app.route("/turmas")
def turmas():

    if not permissao_modulo("Turmas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cargo = session.get("usuario_cargo")
    escola_id = session.get("escola_id")

    # ==========================================
    # ADMINISTRADOR GERAL
    # ==========================================

    if cargo == "Administrador Geral":

        cursor.execute("""
            SELECT
                turmas.*,
                escolas.nome_instituicao
            FROM turmas
            LEFT JOIN escolas
                ON escolas.id = turmas.escola_id
            ORDER BY
                escolas.nome_instituicao,
                turmas.nome
        """)

    # ==========================================
    # DEMAIS USUÁRIOS
    # ==========================================

    else:

        cursor.execute("""
            SELECT *
            FROM turmas
            WHERE escola_id = ?
            ORDER BY nome
        """, (escola_id,))

    lista_turmas = cursor.fetchall()

    banco.close()

    return render_template(
        "gestao/turmas.html",
        turmas=lista_turmas
    )

@app.route("/cadastrar_turma", methods=["POST"])
def cadastrar_turma():

    if not cargo_permitido([
        "Administrador Geral",
        "Administrador da Instituição",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    nome = request.form.get("nome", "").strip()
    ano = request.form.get("ano", "").strip()
    turno = request.form.get("turno", "").strip()

    escola_id = session.get("escola_id")
    cargo = session.get("usuario_cargo")

    if not nome or not ano or not turno:
        flash("Preencha todos os campos.", "erro")
        return redirect("/turmas")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:

        # Administrador Geral deve escolher uma instituição
        if cargo == "Administrador Geral":

            escola_form = request.form.get("escola_id")

            if escola_form:
                escola_id = escola_form

            if not escola_id:
                flash("Selecione uma instituição.", "erro")
                return redirect("/turmas")

        # Verifica se já existe turma com mesmo nome
        cursor.execute("""
            SELECT id
            FROM turmas
            WHERE nome = ?
              AND ano = ?
              AND turno = ?
              AND escola_id = ?
            LIMIT 1
        """, (
            nome,
            ano,
            turno,
            escola_id
        ))

        if cursor.fetchone():
            flash(
                "Já existe uma turma com essas informações.",
                "erro"
            )
            return redirect("/turmas")

        cursor.execute("""
            INSERT INTO turmas
            (
                nome,
                ano,
                turno,
                escola_id
            )
            VALUES (?, ?, ?, ?)
        """, (
            nome,
            ano,
            turno,
            escola_id
        ))

        banco.commit()

        flash(
            "Turma cadastrada com sucesso.",
            "success"
        )

    except Exception as erro:

        banco.rollback()

        print("Erro ao cadastrar turma:", erro)

        flash(
            "Ocorreu um erro ao cadastrar a turma.",
            "erro"
        )

    finally:
        banco.close()

    return redirect("/turmas")

@app.route("/alunos")
def alunos():

    if not permissao_modulo("Alunos"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("SELECT * FROM turmas ORDER BY nome")
    lista_turmas = cursor.fetchall()

    cursor.execute("""
        SELECT alunos.id, alunos.nome, alunos.matricula, turmas.nome
        FROM alunos
        JOIN turmas ON alunos.turma_id = turmas.id
        ORDER BY alunos.nome
    """)
    lista_alunos = cursor.fetchall()

    banco.close()

    return render_template(
        "alunos.html",
        alunos=lista_alunos,
        turmas=lista_turmas
    )

@app.route("/cadastrar_aluno", methods=["POST"])
def cadastrar_aluno():

    if not cargo_permitido([
        "Administrador",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    nome = request.form["nome"]
    matricula = request.form["matricula"]
    turma_id = request.form["turma_id"]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        INSERT INTO alunos (nome, matricula, turma_id)
        VALUES (?, ?, ?)
    """, (nome, matricula, turma_id))

    banco.commit()
    banco.close()

    return redirect("/alunos")

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

    return render_template(
        "professores.html",
        professores=lista_professores,
        turmas=lista_turmas
    )

@app.route("/cadastrar_professor", methods=["POST"])
def cadastrar_professor():

    if not cargo_permitido([
        "Administrador",
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

    if not permissao_modulo("Questoes"):
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
        "Administrador",
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
# EXCLUIR TURMA
# ==========================

@app.route("/excluir_turma/<int:turma_id>")
def excluir_turma(turma_id):

    if not cargo_permitido([
        "Administrador",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "DELETE FROM alunos WHERE turma_id = ?",
        (turma_id,)
    )

    cursor.execute(
        "DELETE FROM turmas WHERE id = ?",
        (turma_id,)
    )

    banco.commit()
    banco.close()

    return redirect("/turmas")


# ==========================
# EDITAR TURMA
# ==========================

@app.route("/editar_turma/<int:turma_id>")
def editar_turma(turma_id):

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "SELECT * FROM turmas WHERE id = ?",
        (turma_id,)
    )

    turma = cursor.fetchone()

    banco.close()

    return render_template(
        "editar_turma.html",
        turma=turma
    )


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
        "Administrador",
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
        "Administrador",
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

@app.route("/gestao/instituicoes")
def gestao_instituicoes():

    # Apenas o Administrador Geral pode acessar
    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cursor.execute("""
        SELECT *
        FROM escolas
        ORDER BY nome_instituicao
    """)

    escolas = cursor.fetchall()

    banco.close()

    return render_template(
        "gestao/gestao_instituicoes.html",
        escolas=escolas
    )

@app.route("/gestao/instituicoes/nova", methods=["GET", "POST"])
def nova_instituicao():

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    if request.method == "POST":

        # ==========================
        # DADOS DO ADMINISTRADOR
        # ==========================

        admin_nome = request.form.get("admin_nome", "").strip()
        admin_email = request.form.get("admin_email", "").strip().lower()
        admin_cpf = request.form.get("admin_cpf", "").strip()
        admin_senha = request.form.get("admin_senha", "").strip()
        admin_senha2 = request.form.get("admin_senha2", "").strip()

        if not admin_nome:
            flash("Informe o nome do administrador.", "erro")
            return render_template("gestao/nova_instituicao.html")

        if not admin_email:
            flash("Informe o e-mail do administrador.", "erro")
            return render_template("gestao/nova_instituicao.html")

        if not admin_senha:
            flash("Informe a senha do administrador.", "erro")
            return render_template("gestao/nova_instituicao.html")

        if admin_senha != admin_senha2:
            flash("As senhas do administrador não conferem.", "erro")
            return render_template("gestao/nova_instituicao.html")

        if len(admin_senha) < 6:
            flash(
                "A senha do administrador deve possuir pelo menos 6 caracteres.",
                "erro"
            )
            return render_template("gestao/nova_instituicao.html")

        # ==========================
        # LOGO
        # ==========================

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

        # ==========================
        # DADOS ACADÊMICOS
        # ==========================

        tipo_instituicao = request.form.get("tipo_instituicao")
        ano_letivo = request.form.get("ano_letivo")

        modalidades = request.form.getlist("modalidade_ensino")
        etapas = request.form.getlist("etapas_ensino")

        modalidade_ensino = ", ".join(modalidades)
        etapas_ensino = ", ".join(etapas)

        banco = conectar_banco()
        banco.row_factory = sqlite3.Row
        cursor = banco.cursor()

        try:
            # Verifica se o e-mail já existe
            cursor.execute("""
                SELECT id
                FROM usuarios
                WHERE LOWER(email) = LOWER(?)
                LIMIT 1
            """, (admin_email,))

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

            # Busca o cargo correto
            cursor.execute("""
                SELECT id
                FROM cargos
                WHERE nome = 'Administrador da Instituição'
                LIMIT 1
            """)

            cargo = cursor.fetchone()

            if cargo is None:
                cursor.execute("""
                    INSERT INTO cargos (nome)
                    VALUES ('Administrador da Instituição')
                """)

                cargo_id = cursor.lastrowid

            else:
                cargo_id = cargo["id"]

            # Cadastra a instituição
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
                request.form.get("nome_instituicao", "").strip(),
                request.form.get("codigo_inep", "").strip(),
                request.form.get("cnpj", "").strip(),
                request.form.get("cep", "").strip(),
                request.form.get("endereco", "").strip(),
                request.form.get("cidade", "").strip(),
                request.form.get("estado", "").strip(),
                request.form.get("telefone", "").strip(),
                request.form.get("whatsapp", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("site", "").strip(),
                request.form.get("diretor", "").strip(),
                request.form.get("coordenador1", "").strip(),
                request.form.get("coordenador2", "").strip(),
                request.form.get("coordenador3", "").strip(),
                request.form.get("secretario", "").strip(),
                tipo_instituicao,
                ano_letivo,
                modalidade_ensino,
                etapas_ensino,
                nome_logo,
                1,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

            escola_id = cursor.lastrowid

            # Cria automaticamente o administrador da escola
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
            banco.close()

            flash(
                "Instituição e administrador cadastrados com sucesso!",
                "success"
            )

            return redirect("/gestao/instituicoes")

        except sqlite3.IntegrityError as erro:
            banco.rollback()
            banco.close()

            flash(
                "Não foi possível cadastrar. Verifique se o e-mail já está em uso.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

        except Exception as erro:
            banco.rollback()
            banco.close()

            print(f"Erro ao cadastrar instituição: {erro}")

            flash(
                "Ocorreu um erro ao cadastrar a instituição.",
                "erro"
            )

            return render_template(
                "gestao/nova_instituicao.html"
            )

    return render_template("gestao/nova_instituicao.html")

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
            ORDER BY usuarios.nome
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
            ORDER BY usuarios.nome
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

    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"].strip()

        banco = conectar_banco()
        banco.row_factory = sqlite3.Row
        cursor = banco.cursor()

        cursor.execute("""
            SELECT usuarios.*,
                   cargos.nome AS cargo
            FROM usuarios
            LEFT JOIN cargos
            ON usuarios.cargo_id = cargos.id
            WHERE usuarios.email = ?
            AND usuarios.senha = ?
            AND usuarios.ativo = 1
        """, (email, senha))

        usuario = cursor.fetchone()
        banco.close()

        if usuario:
            session["usuario_id"] = usuario["id"]
            session["usuario_nome"] = usuario["nome"]
            session["usuario_cargo"] = usuario["cargo"]

            return redirect("/")

        return "Usuário, senha ou status inválido."

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

    cursor.execute(
        "SELECT * FROM escolas WHERE id = ?",
        (id,)
    )

    escola = cursor.fetchone()

    if escola is None:
        banco.close()
        return redirect("/gestao/instituicoes")

    cursor.execute("""
        SELECT *
        FROM usuarios
        WHERE escola_id = ?
        ORDER BY id
        LIMIT 1
    """, (id,))

    administrador = cursor.fetchone()

    if request.method == "POST":

        admin_nome = request.form.get("admin_nome", "").strip()
        admin_email = request.form.get("admin_email", "").strip().lower()
        admin_cpf = request.form.get("admin_cpf", "").strip()
        admin_senha = request.form.get("admin_senha", "").strip()

        if not admin_nome:
            flash("Informe o nome do administrador.", "erro")
            banco.close()
            return redirect(f"/gestao/instituicoes/editar/{id}")

        if not admin_email:
            flash("Informe o e-mail do administrador.", "erro")
            banco.close()
            return redirect(f"/gestao/instituicoes/editar/{id}")

        try:
            # Verifica se o e-mail já pertence a outro usuário
            if administrador:
                cursor.execute("""
                    SELECT id
                    FROM usuarios
                    WHERE LOWER(email) = LOWER(?)
                      AND id != ?
                    LIMIT 1
                """, (
                    admin_email,
                    administrador["id"]
                ))
            else:
                cursor.execute("""
                    SELECT id
                    FROM usuarios
                    WHERE LOWER(email) = LOWER(?)
                    LIMIT 1
                """, (admin_email,))

            email_em_uso = cursor.fetchone()

            if email_em_uso:
                flash(
                    "Este e-mail já está sendo utilizado por outro usuário.",
                    "erro"
                )
                banco.close()
                return redirect(f"/gestao/instituicoes/editar/{id}")

            # Mantém a logo atual se nenhuma nova for enviada
            logo = request.files.get("logo")
            nome_logo = escola["logo"] or ""

            if logo and logo.filename != "":
                nome_logo = secure_filename(logo.filename)

                logo.save(
                    os.path.join(
                        app.config["UPLOAD_FOLDER"],
                        nome_logo
                    )
                )

            modalidades = request.form.getlist("modalidade_ensino")
            etapas = request.form.getlist("etapas_ensino")

            modalidade_ensino = ", ".join(modalidades)
            etapas_ensino = ", ".join(etapas)

            # Atualiza a instituição
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
                request.form.get("nome_instituicao", "").strip(),
                request.form.get("codigo_inep", "").strip(),
                request.form.get("cnpj", "").strip(),
                request.form.get("cep", "").strip(),
                request.form.get("endereco", "").strip(),
                request.form.get("cidade", "").strip(),
                request.form.get("estado", "").strip(),
                request.form.get("telefone", "").strip(),
                request.form.get("whatsapp", "").strip(),
                request.form.get("email", "").strip(),
                request.form.get("site", "").strip(),
                request.form.get("diretor", "").strip(),
                request.form.get("coordenador1", "").strip(),
                request.form.get("coordenador2", "").strip(),
                request.form.get("coordenador3", "").strip(),
                request.form.get("secretario", "").strip(),
                request.form.get("tipo_instituicao"),
                request.form.get("ano_letivo"),
                modalidade_ensino,
                etapas_ensino,
                nome_logo,
                id
            ))

            if administrador:

                if admin_senha:
                    if len(admin_senha) < 6:
                        flash(
                            "A nova senha deve possuir pelo menos 6 caracteres.",
                            "erro"
                        )
                        banco.rollback()
                        banco.close()
                        return redirect(f"/gestao/instituicoes/editar/{id}")

                    cursor.execute("""
                        UPDATE usuarios
                        SET
                            nome = ?,
                            email = ?,
                            cpf = ?,
                            senha = ?,
                            escola_id = ?
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
                        SET
                            nome = ?,
                            email = ?,
                            cpf = ?,
                            escola_id = ?
                        WHERE id = ?
                    """, (
                        admin_nome,
                        admin_email,
                        admin_cpf,
                        id,
                        administrador["id"]
                    ))

            else:
                if not admin_senha:
                    flash(
                        "Informe uma senha para criar o administrador da instituição.",
                        "erro"
                    )
                    banco.rollback()
                    banco.close()
                    return redirect(f"/gestao/instituicoes/editar/{id}")

                if len(admin_senha) < 6:
                    flash(
                        "A senha deve possuir pelo menos 6 caracteres.",
                        "erro"
                    )
                    banco.rollback()
                    banco.close()
                    return redirect(f"/gestao/instituicoes/editar/{id}")

                cursor.execute("""
                    SELECT id
                    FROM cargos
                    WHERE nome = 'Administrador da Instituição'
                    LIMIT 1
                """)

                cargo = cursor.fetchone()

                if cargo is None:
                    cursor.execute("""
                        INSERT INTO cargos (nome)
                        VALUES ('Administrador da Instituição')
                    """)

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
            banco.close()

            flash(
                "Instituição e administrador atualizados com sucesso!",
                "success"
            )

            return redirect("/gestao/instituicoes")

        except sqlite3.IntegrityError:
            banco.rollback()
            banco.close()

            flash(
                "Não foi possível salvar. Verifique se o e-mail já está em uso.",
                "erro"
            )

            return redirect(f"/gestao/instituicoes/editar/{id}")

        except Exception as erro:
            banco.rollback()
            banco.close()

            print(f"Erro ao editar instituição: {erro}")

            flash(
                "Ocorreu um erro ao salvar as alterações.",
                "erro"
            )

            return redirect(f"/gestao/instituicoes/editar/{id}")

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

    banco.close()

    return render_template(
        "gestao/editar_instituicao.html",
        escola=escola,
        administrador=administrador,
        modalidades_marcadas=modalidades_marcadas,
        etapas_marcadas=etapas_marcadas
    )

criar_tabelas()

@app.route("/gestao/instituicoes/ver/<int:id>")
def ver_instituicao(id):

    if not cargo_permitido(["Administrador Geral"]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cursor.execute("""
        SELECT *
        FROM escolas
        WHERE id = ?
    """, (id,))

    escola = cursor.fetchone()

    if escola is None:
        banco.close()
        return redirect("/gestao/instituicoes")

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
        ORDER BY usuarios.id
        LIMIT 1
    """, (id,))

    administrador = cursor.fetchone()

    banco.close()

    return render_template(
        "gestao/ver_instituicao.html",
        escola=escola,
        administrador=administrador
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

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )