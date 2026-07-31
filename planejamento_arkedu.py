import json
import os
import sqlite3
from datetime import datetime
from io import BytesIO

from flask import abort, flash, redirect, render_template, request, send_file, session, url_for


def init_planejamento(app, conectar_banco, obter_contexto_plataforma, cargo_permitido=None):
    """Registra o módulo de Planejamento Pedagógico na aplicação principal."""

    def agora_iso():
        return datetime.now().isoformat(timespec="seconds")

    def garantir_tabelas():
        banco = conectar_banco()
        cursor = banco.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS planejamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                escola_id INTEGER NOT NULL,
                ano_letivo_id INTEGER,
                turma_id INTEGER,
                componente_id INTEGER,
                professor_id INTEGER NOT NULL,
                titulo TEXT NOT NULL DEFAULT 'Plano de Ensino e Aprendizagem (PEA)',
                disciplina TEXT NOT NULL,
                etapa_ensino TEXT,
                ano_serie TEXT,
                area_conhecimento TEXT,
                carga_horaria TEXT,
                apresentacao TEXT,
                objetivo_instituicao TEXT,
                objetivo_disciplina TEXT,
                competencias_componente TEXT,
                contribuicao_estudante TEXT,
                forma_avaliacao TEXT,
                referencias TEXT,
                status TEXT NOT NULL DEFAULT 'Rascunho',
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (escola_id) REFERENCES escolas(id) ON DELETE CASCADE,
                FOREIGN KEY (ano_letivo_id) REFERENCES anos_letivos(id) ON DELETE SET NULL,
                FOREIGN KEY (turma_id) REFERENCES turmas(id) ON DELETE SET NULL,
                FOREIGN KEY (componente_id) REFERENCES componentes_curriculares(id) ON DELETE SET NULL,
                FOREIGN KEY (professor_id) REFERENCES usuarios(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS planejamento_bimestres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                planejamento_id INTEGER NOT NULL,
                numero INTEGER NOT NULL CHECK(numero BETWEEN 1 AND 4),
                unidade TEXT DEFAULT 'Única',
                capitulos TEXT,
                objetos_conhecimento TEXT,
                habilidades TEXT,
                competencias_desenvolver TEXT,
                estrategias_metodologias TEXT,
                materiais_recursos TEXT,
                FOREIGN KEY (planejamento_id) REFERENCES planejamentos(id) ON DELETE CASCADE,
                UNIQUE (planejamento_id, numero)
            );

            CREATE TABLE IF NOT EXISTS planejamento_avaliacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                planejamento_id INTEGER NOT NULL,
                bimestre INTEGER NOT NULL CHECK(bimestre BETWEEN 1 AND 4),
                codigo TEXT,
                tipo_avaliacao TEXT,
                instrumento TEXT,
                peso REAL,
                conteudo TEXT,
                ordem INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (planejamento_id) REFERENCES planejamentos(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS planejamento_cronograma (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                planejamento_id INTEGER NOT NULL,
                bimestre INTEGER NOT NULL CHECK(bimestre BETWEEN 1 AND 4),
                numero_aula INTEGER,
                data_aula TEXT,
                atividade_conteudo TEXT NOT NULL,
                ordem INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (planejamento_id) REFERENCES planejamentos(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_planejamentos_escola_ano
                ON planejamentos(escola_id, ano_letivo_id);
            CREATE INDEX IF NOT EXISTS idx_planejamentos_professor
                ON planejamentos(professor_id);
        """)
        banco.commit()
        banco.close()

    garantir_tabelas()

    def logado():
        return bool(session.get("usuario_id"))

    def contexto_acesso():
        if not logado():
            return None
        return obter_contexto_plataforma()

    def pode_acessar_registro(registro, editar=False):
        cargo = (session.get("usuario_cargo") or "").strip()
        usuario_id = session.get("usuario_id")
        if cargo == "Administrador Geral":
            return True
        escola_id = session.get("escola_id")
        if not escola_id or int(registro["escola_id"]) != int(escola_id):
            return False
        if cargo in {"Administrador da Instituição", "Coordenador", "Secretaria"}:
            return True
        return int(registro["professor_id"]) == int(usuario_id)

    def linha_para_dict(linha):
        return dict(linha) if linha else None

    def carregar_planejamento(cursor, planejamento_id):
        cursor.execute("""
            SELECT p.*, e.nome_instituicao, e.logo, e.cidade, e.estado,
                   e.telefone, e.email AS email_instituicao, e.site,
                   e.diretor, e.coordenador1, e.coordenador2, e.coordenador3,
                   u.nome AS professor_nome, u.email AS professor_email,
                   t.nome AS turma_nome, t.ano AS turma_ano, t.etapa AS turma_etapa,
                   cc.nome AS componente_nome, al.ano AS ano_letivo
            FROM planejamentos p
            JOIN escolas e ON e.id = p.escola_id
            JOIN usuarios u ON u.id = p.professor_id
            LEFT JOIN turmas t ON t.id = p.turma_id
            LEFT JOIN componentes_curriculares cc ON cc.id = p.componente_id
            LEFT JOIN anos_letivos al ON al.id = p.ano_letivo_id
            WHERE p.id = ?
        """, (planejamento_id,))
        return cursor.fetchone()

    def carregar_relacionados(cursor, planejamento_id):
        cursor.execute("SELECT * FROM planejamento_bimestres WHERE planejamento_id=? ORDER BY numero", (planejamento_id,))
        bimestres = {r["numero"]: dict(r) for r in cursor.fetchall()}
        for n in range(1, 5):
            bimestres.setdefault(n, {"numero": n, "unidade": "Única"})
        cursor.execute("SELECT * FROM planejamento_avaliacoes WHERE planejamento_id=? ORDER BY bimestre, ordem, id", (planejamento_id,))
        avaliacoes = {n: [] for n in range(1, 5)}
        for r in cursor.fetchall():
            avaliacoes[r["bimestre"]].append(dict(r))
        cursor.execute("SELECT * FROM planejamento_cronograma WHERE planejamento_id=? ORDER BY bimestre, ordem, numero_aula, id", (planejamento_id,))
        cronograma = {n: [] for n in range(1, 5)}
        for r in cursor.fetchall():
            cronograma[r["bimestre"]].append(dict(r))
        return bimestres, avaliacoes, cronograma

    def opcoes_formulario(cursor, contexto, planejamento=None):
        cargo = (session.get("usuario_cargo") or "").strip()
        escola_id = contexto.get("escola_id") if contexto else None
        if planejamento:
            escola_id = planejamento["escola_id"]

        escolas = []
        if cargo == "Administrador Geral":
            cursor.execute("SELECT id, nome_instituicao FROM escolas WHERE status=1 ORDER BY nome_instituicao")
            escolas = cursor.fetchall()
            escola_id = request.args.get("escola_id", type=int) or request.form.get("escola_id", type=int) or escola_id
        elif escola_id:
            cursor.execute("SELECT id, nome_instituicao FROM escolas WHERE id=?", (escola_id,))
            escolas = cursor.fetchall()

        anos, turmas, componentes, professores = [], [], [], []
        if escola_id:
            cursor.execute("SELECT id, ano, ativo, encerrado FROM anos_letivos WHERE escola_id=? ORDER BY ano DESC", (escola_id,))
            anos = cursor.fetchall()
            # Turmas podem possuir ano_letivo_id em instalações atualizadas.
            cursor.execute("PRAGMA table_info(turmas)")
            colunas_turma = {r[1] for r in cursor.fetchall()}
            if "ano_letivo_id" in colunas_turma and contexto and contexto.get("ano_letivo_id"):
                cursor.execute("SELECT id, nome, ano, etapa, turno FROM turmas WHERE escola_id=? AND ano_letivo_id=? ORDER BY ano, nome", (escola_id, contexto["ano_letivo_id"]))
            else:
                cursor.execute("SELECT id, nome, ano, etapa, turno FROM turmas WHERE escola_id=? ORDER BY ano, nome", (escola_id,))
            turmas = cursor.fetchall()
            cursor.execute("SELECT id, nome, etapa_ensino FROM componentes_curriculares WHERE escola_id=? AND ativo=1 ORDER BY nome", (escola_id,))
            componentes = cursor.fetchall()
            cursor.execute("""
                SELECT u.id, u.nome, u.email
                FROM usuarios u LEFT JOIN cargos c ON c.id=u.cargo_id
                WHERE u.escola_id=? AND u.ativo=1
                  AND (c.nome IN ('Professor','Coordenador','Administrador da Instituição') OR u.id=?)
                ORDER BY u.nome
            """, (escola_id, session.get("usuario_id")))
            professores = cursor.fetchall()
        return escolas, anos, turmas, componentes, professores, escola_id

    def texto(campo):
        return (request.form.get(campo) or "").strip()

    def salvar_relacionados(cursor, planejamento_id):
        for numero in range(1, 5):
            valores = (
                planejamento_id, numero, texto(f"b{numero}_unidade") or "Única",
                texto(f"b{numero}_capitulos"), texto(f"b{numero}_objetos"),
                texto(f"b{numero}_habilidades"), texto(f"b{numero}_competencias"),
                texto(f"b{numero}_estrategias"), texto(f"b{numero}_recursos")
            )
            cursor.execute("""
                INSERT INTO planejamento_bimestres
                (planejamento_id, numero, unidade, capitulos, objetos_conhecimento, habilidades,
                 competencias_desenvolver, estrategias_metodologias, materiais_recursos)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(planejamento_id, numero) DO UPDATE SET
                    unidade=excluded.unidade, capitulos=excluded.capitulos,
                    objetos_conhecimento=excluded.objetos_conhecimento,
                    habilidades=excluded.habilidades,
                    competencias_desenvolver=excluded.competencias_desenvolver,
                    estrategias_metodologias=excluded.estrategias_metodologias,
                    materiais_recursos=excluded.materiais_recursos
            """, valores)

        cursor.execute("DELETE FROM planejamento_avaliacoes WHERE planejamento_id=?", (planejamento_id,))
        cursor.execute("DELETE FROM planejamento_cronograma WHERE planejamento_id=?", (planejamento_id,))
        avaliacoes = json.loads(request.form.get("avaliacoes_json") or "[]")
        for ordem, item in enumerate(avaliacoes):
            bimestre = int(item.get("bimestre") or 1)
            if bimestre not in range(1, 5):
                continue
            peso = item.get("peso")
            try:
                peso = float(str(peso).replace(",", ".")) if str(peso).strip() else None
            except ValueError:
                peso = None
            cursor.execute("""
                INSERT INTO planejamento_avaliacoes
                (planejamento_id,bimestre,codigo,tipo_avaliacao,instrumento,peso,conteudo,ordem)
                VALUES (?,?,?,?,?,?,?,?)
            """, (planejamento_id,bimestre,item.get("codigo",""),item.get("tipo_avaliacao",""),
                  item.get("instrumento",""),peso,item.get("conteudo",""),ordem))
        cronograma = json.loads(request.form.get("cronograma_json") or "[]")
        for ordem, item in enumerate(cronograma):
            bimestre = int(item.get("bimestre") or 1)
            if bimestre not in range(1, 5) or not str(item.get("atividade_conteudo","")).strip():
                continue
            numero_aula = item.get("numero_aula")
            try:
                numero_aula = int(numero_aula) if str(numero_aula).strip() else None
            except ValueError:
                numero_aula = None
            cursor.execute("""
                INSERT INTO planejamento_cronograma
                (planejamento_id,bimestre,numero_aula,data_aula,atividade_conteudo,ordem)
                VALUES (?,?,?,?,?,?)
            """, (planejamento_id,bimestre,numero_aula,item.get("data_aula") or None,
                  str(item.get("atividade_conteudo","")).strip(),ordem))

    @app.route("/planejamentos")
    def planejamentos_listar():
        contexto = contexto_acesso()
        if not contexto:
            return redirect(url_for("login"))
        banco = conectar_banco(); banco.row_factory = sqlite3.Row; cursor = banco.cursor()
        cargo = (session.get("usuario_cargo") or "").strip()
        params = []
        sql = """
            SELECT p.*, e.nome_instituicao, u.nome professor_nome,
                   t.nome turma_nome, COALESCE(cc.nome,p.disciplina) componente_nome,
                   al.ano ano_letivo
            FROM planejamentos p JOIN escolas e ON e.id=p.escola_id
            JOIN usuarios u ON u.id=p.professor_id
            LEFT JOIN turmas t ON t.id=p.turma_id
            LEFT JOIN componentes_curriculares cc ON cc.id=p.componente_id
            LEFT JOIN anos_letivos al ON al.id=p.ano_letivo_id WHERE 1=1
        """
        if cargo != "Administrador Geral":
            sql += " AND p.escola_id=?"; params.append(contexto.get("escola_id"))
        if cargo not in {"Administrador Geral","Administrador da Instituição","Coordenador","Secretaria"}:
            sql += " AND p.professor_id=?"; params.append(session.get("usuario_id"))
        if contexto.get("ano_letivo_id"):
            sql += " AND (p.ano_letivo_id=? OR p.ano_letivo_id IS NULL)"; params.append(contexto["ano_letivo_id"])
        sql += " ORDER BY COALESCE(p.atualizado_em,p.criado_em) DESC"
        cursor.execute(sql, params); registros=cursor.fetchall(); banco.close()
        return render_template("planejamentos/listar.html", planejamentos=registros)

    @app.route("/planejamentos/novo", methods=["GET","POST"])
    def planejamento_novo():
        contexto = contexto_acesso()
        if not contexto: return redirect(url_for("login"))
        banco=conectar_banco(); banco.row_factory=sqlite3.Row; cursor=banco.cursor()
        escolas,anos,turmas,componentes,professores,escola_id=opcoes_formulario(cursor,contexto)
        if request.method == "POST":
            try:
                escola_id = request.form.get("escola_id", type=int) or contexto.get("escola_id")
                professor_id = request.form.get("professor_id", type=int) or session.get("usuario_id")
                disciplina = texto("disciplina")
                if not escola_id or not professor_id or not disciplina:
                    raise ValueError("Informe a instituição, o professor e o componente curricular.")
                cursor.execute("""
                    INSERT INTO planejamentos
                    (escola_id,ano_letivo_id,turma_id,componente_id,professor_id,titulo,disciplina,
                     etapa_ensino,ano_serie,area_conhecimento,carga_horaria,apresentacao,
                     objetivo_instituicao,objetivo_disciplina,competencias_componente,
                     contribuicao_estudante,forma_avaliacao,referencias,status,criado_em,atualizado_em)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (escola_id,request.form.get("ano_letivo_id",type=int),request.form.get("turma_id",type=int),
                      request.form.get("componente_id",type=int),professor_id,texto("titulo") or "Plano de Ensino e Aprendizagem (PEA)",
                      disciplina,texto("etapa_ensino"),texto("ano_serie"),texto("area_conhecimento"),texto("carga_horaria"),
                      texto("apresentacao"),texto("objetivo_instituicao"),texto("objetivo_disciplina"),texto("competencias_componente"),
                      texto("contribuicao_estudante"),texto("forma_avaliacao"),texto("referencias"),texto("status") or "Rascunho",agora_iso(),agora_iso()))
                planejamento_id=cursor.lastrowid; salvar_relacionados(cursor,planejamento_id); banco.commit()
                flash("Planejamento criado com sucesso.","success")
                return redirect(url_for("planejamento_visualizar",planejamento_id=planejamento_id))
            except (ValueError,sqlite3.Error,json.JSONDecodeError) as erro:
                banco.rollback(); flash(f"Não foi possível salvar: {erro}","erro")
        banco.close()
        return render_template("planejamentos/formulario.html", planejamento=None,bimestres={},avaliacoes={},cronograma={},
            escolas=escolas,anos=anos,turmas=turmas,componentes=componentes,professores=professores,escola_id_selecionada=escola_id)

    @app.route("/planejamentos/<int:planejamento_id>")
    def planejamento_visualizar(planejamento_id):
        if not logado(): return redirect(url_for("login"))
        banco=conectar_banco(); banco.row_factory=sqlite3.Row; cursor=banco.cursor()
        registro=carregar_planejamento(cursor,planejamento_id)
        if not registro: banco.close(); abort(404)
        if not pode_acessar_registro(registro): banco.close(); abort(403)
        bimestres,avaliacoes,cronograma=carregar_relacionados(cursor,planejamento_id); banco.close()
        return render_template("planejamentos/visualizar.html",planejamento=registro,bimestres=bimestres,avaliacoes=avaliacoes,cronograma=cronograma)

    @app.route("/planejamentos/<int:planejamento_id>/editar", methods=["GET","POST"])
    def planejamento_editar(planejamento_id):
        contexto=contexto_acesso()
        if not contexto: return redirect(url_for("login"))
        banco=conectar_banco(); banco.row_factory=sqlite3.Row; cursor=banco.cursor()
        registro=carregar_planejamento(cursor,planejamento_id)
        if not registro: banco.close(); abort(404)
        if not pode_acessar_registro(registro,True): banco.close(); abort(403)
        bimestres,avaliacoes,cronograma=carregar_relacionados(cursor,planejamento_id)
        escolas,anos,turmas,componentes,professores,escola_id=opcoes_formulario(cursor,contexto,registro)
        if request.method=="POST":
            try:
                cursor.execute("""
                    UPDATE planejamentos SET ano_letivo_id=?,turma_id=?,componente_id=?,professor_id=?,titulo=?,disciplina=?,
                    etapa_ensino=?,ano_serie=?,area_conhecimento=?,carga_horaria=?,apresentacao=?,objetivo_instituicao=?,
                    objetivo_disciplina=?,competencias_componente=?,contribuicao_estudante=?,forma_avaliacao=?,referencias=?,
                    status=?,atualizado_em=? WHERE id=?
                """, (request.form.get("ano_letivo_id",type=int),request.form.get("turma_id",type=int),request.form.get("componente_id",type=int),
                      request.form.get("professor_id",type=int) or registro["professor_id"],texto("titulo"),texto("disciplina"),texto("etapa_ensino"),
                      texto("ano_serie"),texto("area_conhecimento"),texto("carga_horaria"),texto("apresentacao"),texto("objetivo_instituicao"),
                      texto("objetivo_disciplina"),texto("competencias_componente"),texto("contribuicao_estudante"),texto("forma_avaliacao"),
                      texto("referencias"),texto("status") or "Rascunho",agora_iso(),planejamento_id))
                salvar_relacionados(cursor,planejamento_id); banco.commit(); flash("Planejamento atualizado com sucesso.","success")
                return redirect(url_for("planejamento_visualizar",planejamento_id=planejamento_id))
            except (ValueError,sqlite3.Error,json.JSONDecodeError) as erro:
                banco.rollback(); flash(f"Não foi possível atualizar: {erro}","erro")
        banco.close()
        return render_template("planejamentos/formulario.html",planejamento=registro,bimestres=bimestres,avaliacoes=avaliacoes,cronograma=cronograma,
            escolas=escolas,anos=anos,turmas=turmas,componentes=componentes,professores=professores,escola_id_selecionada=escola_id)

    @app.route("/planejamentos/<int:planejamento_id>/excluir",methods=["POST"])
    def planejamento_excluir(planejamento_id):
        if not logado(): return redirect(url_for("login"))
        banco=conectar_banco(); banco.row_factory=sqlite3.Row; cursor=banco.cursor(); registro=carregar_planejamento(cursor,planejamento_id)
        if not registro: banco.close(); abort(404)
        if not pode_acessar_registro(registro,True): banco.close(); abort(403)
        cursor.execute("DELETE FROM planejamentos WHERE id=?",(planejamento_id,)); banco.commit(); banco.close()
        flash("Planejamento excluído com sucesso.","success"); return redirect(url_for("planejamentos_listar"))

    @app.route("/planejamentos/<int:planejamento_id>/imprimir")
    def planejamento_imprimir(planejamento_id):
        if not logado(): return redirect(url_for("login"))
        banco=conectar_banco(); banco.row_factory=sqlite3.Row; cursor=banco.cursor(); registro=carregar_planejamento(cursor,planejamento_id)
        if not registro: banco.close(); abort(404)
        if not pode_acessar_registro(registro): banco.close(); abort(403)
        bimestres,avaliacoes,cronograma=carregar_relacionados(cursor,planejamento_id); banco.close()
        return render_template("planejamentos/pdf.html",planejamento=registro,bimestres=bimestres,avaliacoes=avaliacoes,cronograma=cronograma,modo_impressao=True)

    @app.route("/planejamentos/<int:planejamento_id>/pdf")
    def planejamento_pdf(planejamento_id):
        if not logado(): return redirect(url_for("login"))
        banco=conectar_banco(); banco.row_factory=sqlite3.Row; cursor=banco.cursor(); registro=carregar_planejamento(cursor,planejamento_id)
        if not registro: banco.close(); abort(404)
        if not pode_acessar_registro(registro): banco.close(); abort(403)
        bimestres,avaliacoes,cronograma=carregar_relacionados(cursor,planejamento_id); banco.close()
        html=render_template("planejamentos/pdf.html",planejamento=registro,bimestres=bimestres,avaliacoes=avaliacoes,cronograma=cronograma,modo_impressao=False)
        try:
            from weasyprint import HTML
            pdf=HTML(string=html,base_url=request.url_root).write_pdf()
            nome=f"planejamento_{planejamento_id}_{(registro['disciplina'] or 'componente').replace(' ','_')}.pdf"
            return send_file(BytesIO(pdf),mimetype="application/pdf",as_attachment=True,download_name=nome)
        except Exception as erro:
            app.logger.exception("Falha ao gerar PDF com WeasyPrint")
            flash("O gerador de PDF não está disponível no servidor. Use a opção Imprimir/Salvar em PDF.","aviso")
            return redirect(url_for("planejamento_imprimir",planejamento_id=planejamento_id))
