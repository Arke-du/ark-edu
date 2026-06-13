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
def inicio():

    if "usuario_id" not in session:
        return redirect("/login")

    return render_template(
        "index.html",
        usuario_nome=session.get("usuario_nome"),
        usuario_cargo=session.get("usuario_cargo")
    )

@app.route("/esqueci_senha")
def esqueci_senha():
    return render_template("esqueci_senha.html")

@app.route("/turmas")
def turmas():

    if not permissao_modulo("Turmas"):
        return redirect("/acesso_negado")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("SELECT * FROM turmas ORDER BY nome")
    lista_turmas = cursor.fetchall()

    banco.close()

    return render_template(
        "turmas.html",
        turmas=lista_turmas
    )

@app.route("/cadastrar_turma", methods=["POST"])
def cadastrar_turma():

    if not cargo_permitido([
        "Administrador",
        "Coordenador",
        "Secretaria"
    ]):
        return redirect("/login")

    nome = request.form["nome"]
    ano = request.form["ano"]
    turno = request.form["turno"]

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        INSERT INTO turmas (nome, ano, turno)
        VALUES (?, ?, ?)
    """, (nome, ano, turno))

    banco.commit()
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
        return f"Erro ao abrir imagem: {erro}"

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

@app.route("/usuarios")
def usuarios():

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    banco = conectar_banco()

    banco.row_factory = sqlite3.Row

    cursor = banco.cursor()

    cursor.execute("""
        SELECT
            usuarios.id,
            usuarios.nome,
            usuarios.email,
            usuarios.senha,
            usuarios.ativo,
            cargos.nome AS cargo

        FROM usuarios

        LEFT JOIN cargos
        ON usuarios.cargo_id = cargos.id

        ORDER BY usuarios.nome
    """)

    usuarios = cursor.fetchall()

    banco.close()

    return render_template(
        "usuarios.html",
        usuarios=usuarios
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

@app.route("/cadastrar_usuario", methods=["GET", "POST"])
def cadastrar_usuario():

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cursor.execute("SELECT * FROM cargos ORDER BY nome")
    cargos = cursor.fetchall()

    if request.method == "POST":
        nome = request.form["nome"]
        email = request.form["email"]
        senha = request.form["senha"]
        cargo_id = request.form["cargo_id"]

        try:
            cursor.execute("""
                INSERT INTO usuarios (nome, email, senha, cargo_id, ativo)
                VALUES (?, ?, ?, ?, 1)
            """, (nome, email, senha, cargo_id))

            banco.commit()
            banco.close()

            return redirect("/gestao")

        except sqlite3.IntegrityError:
            banco.close()
            return "Erro: já existe um usuário com esse e-mail."

    banco.close()

    return render_template(
        "cadastrar_usuario.html",
        cargos=cargos
    )

@app.route("/editar_usuario/<int:id>", methods=["GET", "POST"])
def editar_usuario(id):

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cursor.execute("SELECT * FROM cargos ORDER BY nome")
    cargos = cursor.fetchall()

    cursor.execute("SELECT * FROM usuarios WHERE id = ?", (id,))
    usuario = cursor.fetchone()

    if request.method == "POST":
        nome = request.form["nome"]
        email = request.form["email"]
        senha = request.form["senha"]
        cargo_id = request.form["cargo_id"]

        cursor.execute("""
            UPDATE usuarios
            SET nome = ?, email = ?, senha = ?, cargo_id = ?
            WHERE id = ?
        """, (nome, email, senha, cargo_id, id))

        banco.commit()
        banco.close()

        return redirect("/gestao")

    banco.close()

    return render_template(
        "editar_usuario.html",
        usuario=usuario,
        cargos=cargos
    )

@app.route("/excluir_usuario/<int:id>")
def excluir_usuario(id):

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute(
        "DELETE FROM usuarios WHERE id = ?",
        (id,)
    )

    banco.commit()
    banco.close()

    return redirect("/gestao")

@app.route("/ativar_inativar_usuario/<int:id>")
def ativar_inativar_usuario(id):

    if not cargo_permitido(["Administrador"]):
        return redirect("/login")

    banco = conectar_banco()
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    cursor.execute(
        "SELECT ativo FROM usuarios WHERE id = ?",
        (id,)
    )

    usuario = cursor.fetchone()

    if not usuario:
        banco.close()
        return redirect("/gestao")

    if usuario["ativo"] == 1:
        novo_status = 0
    else:
        novo_status = 1

    cursor.execute("""
        UPDATE usuarios
        SET ativo = ?
        WHERE id = ?
    """, (novo_status, id))

    banco.commit()
    banco.close()

    return redirect("/gestao")

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

    banco = conectar_banco()
    cursor = banco.cursor()

    cursor.execute("""
        SELECT cargo_id
        FROM usuarios
        WHERE id = ?
    """, (session["usuario_id"],))

    usuario = cursor.fetchone()

    if not usuario:
        banco.close()
        return False

    cargo_id = usuario[0]

    cursor.execute("""
        SELECT 1
        FROM permissoes
        WHERE cargo_id = ?
        AND modulo = ?
        AND pode_acessar = 1
    """, (cargo_id, modulo))

    permissao = cursor.fetchone()

    banco.close()

    return permissao is not None

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

if __name__ == "__main__":
    app.run(debug=False)