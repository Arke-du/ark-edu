"""Módulo SaaS da ARK EDUS.

Adiciona cadastro de professor autônomo, planos, assinaturas e limites sem
alterar o fluxo das instituições já existentes.
"""
from datetime import datetime, date, timedelta, timezone
import os
import sqlite3
import uuid
from flask import flash, redirect, render_template, request, session, url_for, jsonify
from werkzeug.security import generate_password_hash
from services.plano_service import (
    obter_conta, assinatura_ativa, possui_recurso, motivo_recurso,
    verificar_limite, usa_marca_dagua, isencao_vigente, teste_vigente, dias_restantes_teste
)

PLANOS = {
    "gratuito": {
        "nome": "Gratuito", "valor": 0.0, "tipo": "professor",
        "limite_turmas": 1, "limite_alunos": 50, "limite_provas_mes": 2,
        "marca_dagua": 1, "suporte_prioritario": 0,
    },
    "professor": {
        "nome": "Professor", "valor": 39.90, "tipo": "professor",
        "limite_turmas": None, "limite_alunos": None, "limite_provas_mes": None,
        "marca_dagua": 0, "suporte_prioritario": 1,
    },
    "start": {
        "nome": "Start", "valor": 199.0, "tipo": "instituicao",
        "limite_turmas": None, "limite_alunos": 300, "limite_provas_mes": None,
        "marca_dagua": 0, "suporte_prioritario": 0,
    },
    "essencial": {
        "nome": "Essencial", "valor": 299.0, "tipo": "instituicao",
        "limite_turmas": None, "limite_alunos": 700, "limite_provas_mes": None,
        "marca_dagua": 0, "suporte_prioritario": 1,
    },
}


def _cols(cur, table):
    return {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def migrar(conectar_banco):
    db = conectar_banco(); cur = db.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS planos_saas (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      codigo TEXT UNIQUE NOT NULL,
      nome TEXT NOT NULL,
      tipo_cliente TEXT NOT NULL,
      valor_mensal REAL NOT NULL DEFAULT 0,
      limite_turmas INTEGER,
      limite_alunos INTEGER,
      limite_provas_mes INTEGER,
      marca_dagua INTEGER NOT NULL DEFAULT 0,
      suporte_prioritario INTEGER NOT NULL DEFAULT 0,
      ativo INTEGER NOT NULL DEFAULT 1,
      criado_em TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS assinaturas_saas (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      usuario_id INTEGER NOT NULL,
      escola_id INTEGER,
      plano_codigo TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'ativa',
      gateway TEXT,
      gateway_customer_id TEXT,
      gateway_subscription_id TEXT,
      inicio_em TEXT DEFAULT CURRENT_TIMESTAMP,
      proxima_cobranca TEXT,
      cancelada_em TEXT,
      atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
      FOREIGN KEY(escola_id) REFERENCES escolas(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS pagamentos_saas (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      assinatura_id INTEGER NOT NULL,
      gateway TEXT,
      gateway_payment_id TEXT,
      forma_pagamento TEXT,
      valor REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'pendente',
      vencimento TEXT,
      pago_em TEXT,
      criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(assinatura_id) REFERENCES assinaturas_saas(id) ON DELETE CASCADE
    );
    """)
    for col, definition in {
        "isento": "INTEGER NOT NULL DEFAULT 0",
        "motivo_isencao": "TEXT",
        "isencao_inicio": "TEXT",
        "isencao_fim": "TEXT",
        "concedida_por": "INTEGER",
        "metodo_pagamento": "TEXT",
        "renovacao_automatica": "INTEGER NOT NULL DEFAULT 1",
        "observacao_cobranca": "TEXT",
        "teste_inicio": "TEXT",
        "teste_fim": "TEXT",
    }.items():
        if col not in _cols(cur, "assinaturas_saas"):
            cur.execute(f"ALTER TABLE assinaturas_saas ADD COLUMN {col} {definition}")

    for col, definition in {
        "tipo_conta": "TEXT DEFAULT 'institucional'",
        "plano_codigo": "TEXT",
        "assinatura_status": "TEXT DEFAULT 'ativa'",
        "cadastro_autonomo": "INTEGER DEFAULT 0",
    }.items():
        if col not in _cols(cur, "usuarios"):
            cur.execute(f"ALTER TABLE usuarios ADD COLUMN {col} {definition}")
    if "workspace_autonomo" not in _cols(cur, "escolas"):
        cur.execute("ALTER TABLE escolas ADD COLUMN workspace_autonomo INTEGER DEFAULT 0")
    if "plano_codigo" not in _cols(cur, "escolas"):
        cur.execute("ALTER TABLE escolas ADD COLUMN plano_codigo TEXT")

    cur.execute("INSERT OR IGNORE INTO cargos(nome) VALUES ('Professor Autônomo')")

    # Contas antigas que já possuíam o cargo de Professor Autônomo foram
    # criadas antes das colunas SaaS. O DEFAULT institucional não pode
    # transformar essas contas em instituições comuns durante a migração.
    cur.execute("""
        UPDATE usuarios
        SET tipo_conta = 'autonomo',
            cadastro_autonomo = 1,
            plano_codigo = COALESCE(NULLIF(plano_codigo, ''), 'gratuito'),
            assinatura_status = COALESCE(NULLIF(assinatura_status, ''), 'ativa')
        WHERE cargo_id = (SELECT id FROM cargos WHERE nome = 'Professor Autônomo' LIMIT 1)
    """)

    for codigo, p in PLANOS.items():
        cur.execute("""INSERT INTO planos_saas(codigo,nome,tipo_cliente,valor_mensal,
            limite_turmas,limite_alunos,limite_provas_mes,marca_dagua,suporte_prioritario,ativo)
            VALUES(?,?,?,?,?,?,?,?,?,1)
            ON CONFLICT(codigo) DO UPDATE SET nome=excluded.nome, tipo_cliente=excluded.tipo_cliente,
            valor_mensal=excluded.valor_mensal, limite_turmas=excluded.limite_turmas,
            limite_alunos=excluded.limite_alunos, limite_provas_mes=excluded.limite_provas_mes,
            marca_dagua=excluded.marca_dagua, suporte_prioritario=excluded.suporte_prioritario""",
            (codigo,p['nome'],p['tipo'],p['valor'],p['limite_turmas'],p['limite_alunos'],
             p['limite_provas_mes'],p['marca_dagua'],p['suporte_prioritario']))
    db.commit(); db.close()


def init_saas(app, conectar_banco, sincronizar_ano_letivo_instituicao=None):
    migrar(conectar_banco)

    def atualizar_teste_expirado(usuario_id):
        """Converte automaticamente o teste vencido em Plano Gratuito."""
        if not usuario_id:
            return
        db = conectar_banco(); db.row_factory = sqlite3.Row; cur = db.cursor()
        try:
            row = cur.execute("""
                SELECT a.id, a.escola_id, a.status, a.teste_fim, COALESCE(a.isento,0) AS isento,
                       u.tipo_conta
                FROM assinaturas_saas a
                JOIN usuarios u ON u.id=a.usuario_id
                WHERE a.usuario_id=?
                ORDER BY a.id DESC LIMIT 1
            """, (usuario_id,)).fetchone()
            if not row or row['tipo_conta'] != 'autonomo' or row['status'] != 'trial' or row['isento']:
                return
            try:
                fim = datetime.fromisoformat(str(row['teste_fim']).replace('Z','+00:00'))
                if fim.tzinfo is None:
                    fim = fim.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                fim = datetime.now(timezone.utc)
            if datetime.now(timezone.utc) < fim.astimezone(timezone.utc):
                return
            cur.execute("""UPDATE assinaturas_saas
                SET plano_codigo='gratuito', status='gratuita', gateway='gratuito',
                    metodo_pagamento=NULL, proxima_cobranca=NULL, atualizado_em=CURRENT_TIMESTAMP
                WHERE id=?""", (row['id'],))
            cur.execute("""UPDATE usuarios
                SET plano_codigo='gratuito', assinatura_status='gratuita' WHERE id=?""", (usuario_id,))
            if row['escola_id']:
                cur.execute("UPDATE escolas SET plano_codigo='gratuito' WHERE id=?", (row['escola_id'],))
            db.commit()
        except Exception:
            db.rollback(); app.logger.exception('Erro ao encerrar teste gratuito')
        finally:
            db.close()

    def assinatura_usuario(usuario_id):
        atualizar_teste_expirado(usuario_id)
        return obter_conta(conectar_banco, usuario_id)

    def garantir_workspace_autonomo(usuario_id):
        """Garante um espaço escolar interno para o professor autônomo.

        Turmas, alunos, provas e anos letivos usam escola_id no modelo legado.
        Em vez de duplicar todas as rotas, cada autônomo recebe um workspace
        privado, invisível como instituição comercial. A rotina também repara
        contas antigas com escola removida, inativa ou sem ano letivo.
        """
        if not usuario_id:
            return None

        db = conectar_banco(); db.row_factory = sqlite3.Row; cur = db.cursor()
        try:
            usuario = cur.execute("""
                SELECT u.id, u.nome, u.email, u.telefone, u.escola_id,
                       u.tipo_conta, u.plano_codigo, c.nome AS cargo
                FROM usuarios u
                LEFT JOIN cargos c ON c.id = u.cargo_id
                WHERE u.id = ?
                LIMIT 1
            """, (usuario_id,)).fetchone()

            if not usuario or not (usuario['cargo'] == 'Professor Autônomo' or usuario['tipo_conta'] == 'autonomo'):
                return usuario['escola_id'] if usuario else None

            # Corrige a classificação SaaS de cadastros anteriores.
            cur.execute("""
                UPDATE usuarios
                SET tipo_conta='autonomo', cadastro_autonomo=1,
                    plano_codigo=COALESCE(NULLIF(plano_codigo,''),'gratuito'),
                    assinatura_status=COALESCE(NULLIF(assinatura_status,''),'ativa')
                WHERE id=?
            """, (usuario_id,))

            escola_id = usuario['escola_id']
            escola = None
            if escola_id:
                escola = cur.execute("SELECT id FROM escolas WHERE id=? LIMIT 1", (escola_id,)).fetchone()

            if not escola:
                ano = datetime.now().year
                cur.execute("""
                    INSERT INTO escolas (nome_instituicao, email, telefone, tipo_instituicao,
                        ano_letivo, status, criado_em, workspace_autonomo, plano_codigo)
                    VALUES (?, ?, ?, 'Professor Autônomo', ?, 1, CURRENT_TIMESTAMP, 1, ?)
                """, (f"Espaço de {usuario['nome']}", usuario['email'], usuario['telefone'],
                      str(ano), usuario['plano_codigo'] or 'gratuito'))
                escola_id = cur.lastrowid
                cur.execute("UPDATE usuarios SET escola_id=? WHERE id=?", (escola_id, usuario_id))
            else:
                cur.execute("""
                    UPDATE escolas
                    SET status=1, workspace_autonomo=1,
                        plano_codigo=COALESCE(NULLIF(plano_codigo,''), ?),
                        tipo_instituicao=COALESCE(NULLIF(tipo_instituicao,''),'Professor Autônomo')
                    WHERE id=?
                """, (usuario['plano_codigo'] or 'gratuito', escola_id))

            ano = datetime.now().year
            if sincronizar_ano_letivo_instituicao:
                sincronizar_ano_letivo_instituicao(cur, escola_id, ano, tornar_ativo=True)
            else:
                existe = cur.execute("SELECT id FROM anos_letivos WHERE escola_id=? AND ano=? LIMIT 1", (escola_id, ano)).fetchone()
                if not existe:
                    cur.execute("INSERT INTO anos_letivos(escola_id,ano,ativo,encerrado) VALUES(?,?,1,0)", (escola_id, ano))

            # Cria uma assinatura gratuita legada quando ainda não existe.
            assinatura = cur.execute("SELECT id FROM assinaturas_saas WHERE usuario_id=? LIMIT 1", (usuario_id,)).fetchone()
            if not assinatura:
                plano = usuario['plano_codigo'] or 'gratuito'
                status = 'ativa' if plano == 'gratuito' else 'pendente'
                cur.execute("""INSERT INTO assinaturas_saas
                    (usuario_id, escola_id, plano_codigo, status, gateway)
                    VALUES(?,?,?,?,?)""",
                    (usuario_id, escola_id, plano, status, 'gratuito' if plano == 'gratuito' else 'manual'))

            db.commit()
            return int(escola_id)
        except Exception:
            db.rollback()
            app.logger.exception('Erro ao preparar workspace do professor autônomo')
            return None
        finally:
            db.close()

    # Repara automaticamente todos os professores autônomos já existentes.
    db_reparo = conectar_banco(); db_reparo.row_factory = sqlite3.Row
    try:
        ids_autonomos = [r['id'] for r in db_reparo.execute("""
            SELECT u.id FROM usuarios u LEFT JOIN cargos c ON c.id=u.cargo_id
            WHERE c.nome='Professor Autônomo' OR COALESCE(u.tipo_conta,'')='autonomo'
        """).fetchall()]
    finally:
        db_reparo.close()
    for _uid in ids_autonomos:
        garantir_workspace_autonomo(_uid)

    app.jinja_env.globals['planos_arkedu'] = PLANOS
    app.jinja_env.globals['tem_recurso_plano'] = lambda recurso: possui_recurso(
        assinatura_usuario(session.get('usuario_id')) if session.get('usuario_id') else None, recurso
    )

    @app.context_processor
    def contexto_saas():
        info = assinatura_usuario(session['usuario_id']) if session.get('usuario_id') else None
        usuario_logado = None
        if session.get('usuario_id'):
            db_usuario = conectar_banco(); db_usuario.row_factory = sqlite3.Row
            try:
                usuario_logado = db_usuario.execute("""
                    SELECT u.id, u.nome, u.email, u.escola_id, u.tipo_conta,
                           c.nome AS cargo
                    FROM usuarios u
                    LEFT JOIN cargos c ON c.id=u.cargo_id
                    WHERE u.id=? AND u.ativo=1
                    LIMIT 1
                """, (session['usuario_id'],)).fetchone()
            finally:
                db_usuario.close()
        return {
            'usuario_logado': usuario_logado,
            'conta_saas': info,
            'eh_professor_autonomo': bool(info and info['tipo_conta']=='autonomo'),
            'plano_tem_recurso': (lambda recurso: possui_recurso(info, recurso)),
            'exibir_marca_dagua_arkedu': usa_marca_dagua(info),
            'conta_isenta': isencao_vigente(info),
            'conta_em_teste': teste_vigente(info),
            'dias_teste_restantes': dias_restantes_teste(info),
        }

    @app.route('/assinatura/planos')
    def pagina_planos():
        return render_template('site/planos.html', planos=PLANOS)

    @app.route('/cadastre-se', methods=['GET','POST'])
    def cadastro_professor_autonomo():
        if request.method=='GET':
            return render_template('saas/cadastro_professor.html', plano=request.args.get('plano','gratuito'))
        nome=request.form.get('nome','').strip(); email=request.form.get('email','').strip().lower()
        cpf=request.form.get('cpf','').strip(); telefone=request.form.get('telefone','').strip()
        senha=request.form.get('senha',''); confirmar=request.form.get('confirmar_senha','')
        plano=request.form.get('plano','gratuito').strip().lower()
        if plano not in ('gratuito','professor'): plano='gratuito'
        if not nome or not email or not senha:
            flash('Preencha nome, e-mail e senha.','erro'); return render_template('saas/cadastro_professor.html',plano=plano)
        if senha != confirmar:
            flash('As senhas não coincidem.','erro'); return render_template('saas/cadastro_professor.html',plano=plano)
        if len(senha)<6:
            flash('A senha deve ter pelo menos 6 caracteres.','erro'); return render_template('saas/cadastro_professor.html',plano=plano)
        db=conectar_banco(); db.row_factory=sqlite3.Row; cur=db.cursor()
        try:
            if cur.execute('SELECT 1 FROM usuarios WHERE lower(email)=?',(email,)).fetchone():
                flash('Já existe uma conta com esse e-mail.','erro'); return render_template('saas/cadastro_professor.html',plano=plano)
            ano=datetime.now().year
            # Todo novo professor autônomo começa com 7 dias do Plano Professor.
            plano_trial='professor'
            teste_inicio=datetime.now(timezone.utc)
            teste_fim=teste_inicio + timedelta(days=7)
            cur.execute("""INSERT INTO escolas(nome_instituicao,email,telefone,tipo_instituicao,
                ano_letivo,status,criado_em,workspace_autonomo,plano_codigo)
                VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP,1,?)""",
                (f'Espaço de {nome}',email,telefone,'Professor Autônomo',str(ano),plano_trial))
            escola_id=cur.lastrowid
            if sincronizar_ano_letivo_instituicao:
                sincronizar_ano_letivo_instituicao(cur, escola_id, ano, tornar_ativo=True)
            cargo=cur.execute("SELECT id FROM cargos WHERE nome='Professor Autônomo'").fetchone()
            cur.execute("""INSERT INTO usuarios(nome,email,senha,cargo_id,ativo,escola_id,cpf,telefone,
                tipo_conta,plano_codigo,assinatura_status,cadastro_autonomo)
                VALUES(?,?,?,?,1,?,?,?,?,?,?,1)""",
                (nome,email,generate_password_hash(senha),cargo['id'],escola_id,cpf,telefone,'autonomo',plano_trial,'trial'))
            usuario_id=cur.lastrowid
            cur.execute("""INSERT INTO assinaturas_saas
                (usuario_id,escola_id,plano_codigo,status,gateway,teste_inicio,teste_fim)
                VALUES(?,?,?,?,?,?,?)""",
                (usuario_id,escola_id,plano_trial,'trial','trial',
                 teste_inicio.isoformat(),teste_fim.isoformat()))
            db.commit()

            # O cadastro pode ser aberto enquanto outra conta ainda está
            # autenticada no mesmo navegador. Limpa a sessão anterior para
            # impedir que o botão "Ir para o login" retorne ao dashboard do
            # usuário antigo (por exemplo, Luana). O novo professor deverá
            # autenticar-se normalmente com as credenciais recém-criadas.
            session.clear()
            return redirect(url_for('cadastro_concluido'))
        except Exception as e:
            db.rollback(); app.logger.exception('Erro cadastro autônomo')
            flash(f'Não foi possível criar a conta: {e}','erro'); return render_template('saas/cadastro_professor.html',plano=plano)
        finally: db.close()

    @app.route('/cadastro-concluido')
    def cadastro_concluido():
        return render_template('saas/cadastro_concluido.html')

    @app.route('/checkout/professor')
    def checkout_professor():
        uid=session.get('cadastro_pagamento_usuario_id')
        if not uid: return redirect(url_for('cadastro_professor_autonomo',plano='professor'))
        return render_template('saas/checkout_professor.html', valor=39.90,
            asaas_configurado=bool(os.environ.get('ASAAS_API_KEY')))

    @app.route('/checkout/professor/aguardando', methods=['POST'])
    def checkout_aguardando():
        uid=session.get('cadastro_pagamento_usuario_id')
        metodo=(request.form.get('metodo_pagamento') or 'pix').strip().lower()
        if metodo not in {'pix','boleto','credit_card'}:
            metodo='pix'
        if uid:
            db=conectar_banco(); cur=db.cursor()
            try:
                a=cur.execute('SELECT id FROM assinaturas_saas WHERE usuario_id=? ORDER BY id DESC LIMIT 1',(uid,)).fetchone()
                if a:
                    cur.execute("UPDATE assinaturas_saas SET metodo_pagamento=?, renovacao_automatica=1, atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(metodo,a[0]))
                    cur.execute("UPDATE pagamentos_saas SET forma_pagamento=? WHERE assinatura_id=? AND status='pendente'",(metodo,a[0]))
                    db.commit()
            finally:
                db.close()
        flash('Cadastro realizado. Sua conta será liberada automaticamente após a confirmação do pagamento.','sucesso')
        session.pop('cadastro_pagamento_usuario_id',None)
        return redirect(url_for('login'))

    @app.route('/api/asaas/webhook', methods=['POST'])
    def asaas_webhook():
        token=os.environ.get('ASAAS_WEBHOOK_TOKEN')
        if token and request.headers.get('asaas-access-token') != token:
            return jsonify({'erro':'não autorizado'}),401
        data=request.get_json(silent=True) or {}; payment=data.get('payment') or {}
        event=str(data.get('event',''))
        ext=str(payment.get('id','')); customer=str(payment.get('customer',''))
        if event not in {'PAYMENT_RECEIVED','PAYMENT_CONFIRMED'}:
            return jsonify({'ok':True})
        db=conectar_banco(); cur=db.cursor()
        try:
            row=None
            if ext:
                row=cur.execute("SELECT assinatura_id FROM pagamentos_saas WHERE gateway_payment_id=?",(ext,)).fetchone()
            if not row and customer:
                row=cur.execute("SELECT id FROM assinaturas_saas WHERE gateway_customer_id=? ORDER BY id DESC LIMIT 1",(customer,)).fetchone()
            if row:
                aid=row[0]
                cur.execute("UPDATE pagamentos_saas SET status='pago',pago_em=CURRENT_TIMESTAMP,gateway_payment_id=COALESCE(gateway_payment_id,?) WHERE assinatura_id=?",(ext,aid))
                cur.execute("UPDATE assinaturas_saas SET status='ativa',atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(aid,))
                cur.execute("UPDATE usuarios SET assinatura_status='ativa' WHERE id=(SELECT usuario_id FROM assinaturas_saas WHERE id=?)",(aid,))
                db.commit()
            return jsonify({'ok':True})
        finally: db.close()

    @app.route('/gestao/cortesias', methods=['GET', 'POST'])
    def gestao_cortesias():
        if session.get('usuario_cargo', '').strip() != 'Administrador Geral':
            flash('Apenas o Administrador Geral pode gerenciar cortesias e isenções.', 'erro')
            return redirect('/dashboard')

        db = conectar_banco(); db.row_factory = sqlite3.Row; cur = db.cursor()
        try:
            if request.method == 'POST':
                tipo = (request.form.get('tipo_conta') or '').strip()
                registro_id = request.form.get('registro_id', type=int)
                plano = (request.form.get('plano_codigo') or '').strip().lower()
                isento = 1 if request.form.get('isento') == '1' else 0
                motivo = (request.form.get('motivo_isencao') or '').strip() or None
                inicio = (request.form.get('isencao_inicio') or '').strip() or date.today().isoformat()
                fim = (request.form.get('isencao_fim') or '').strip() or None

                if tipo not in {'professor', 'instituicao'} or not registro_id:
                    flash('Selecione uma conta válida.', 'erro')
                    return redirect(url_for('gestao_cortesias'))
                permitidos = {'professor'} if tipo == 'professor' else {'start', 'essencial'}
                if plano not in permitidos:
                    flash('Selecione um plano compatível com o tipo de conta.', 'erro')
                    return redirect(url_for('gestao_cortesias'))
                if fim and fim < inicio:
                    flash('A data final não pode ser anterior à data inicial.', 'erro')
                    return redirect(url_for('gestao_cortesias'))

                if tipo == 'professor':
                    alvo = cur.execute("""SELECT id, escola_id FROM usuarios
                        WHERE id=? AND COALESCE(tipo_conta,'institucional')='autonomo'""", (registro_id,)).fetchone()
                    if not alvo:
                        flash('Professor autônomo não encontrado.', 'erro')
                        return redirect(url_for('gestao_cortesias'))
                    usuario_id, escola_id = alvo['id'], alvo['escola_id']
                    cur.execute("UPDATE usuarios SET plano_codigo=?, assinatura_status=? WHERE id=?",
                                (plano, 'isenta' if isento else 'pendente', usuario_id))
                    cur.execute("UPDATE escolas SET plano_codigo=? WHERE id=?", (plano, escola_id))
                else:
                    escola_id = registro_id
                    escola = cur.execute("SELECT id FROM escolas WHERE id=? AND COALESCE(workspace_autonomo,0)=0", (escola_id,)).fetchone()
                    if not escola:
                        flash('Instituição não encontrada.', 'erro')
                        return redirect(url_for('gestao_cortesias'))
                    admin = cur.execute("""SELECT u.id FROM usuarios u
                        LEFT JOIN cargos c ON c.id=u.cargo_id
                        WHERE u.escola_id=?
                        ORDER BY CASE WHEN c.nome='Administrador da Instituição' THEN 0 ELSE 1 END, u.id
                        LIMIT 1""", (escola_id,)).fetchone()
                    if not admin:
                        flash('A instituição precisa ter pelo menos um usuário para receber a assinatura.', 'erro')
                        return redirect(url_for('gestao_cortesias'))
                    usuario_id = admin['id']
                    cur.execute("UPDATE escolas SET plano_codigo=? WHERE id=?", (plano, escola_id))
                    cur.execute("UPDATE usuarios SET plano_codigo=?, assinatura_status=? WHERE escola_id=?",
                                (plano, 'isenta' if isento else 'pendente', escola_id))

                assinatura = cur.execute("SELECT id FROM assinaturas_saas WHERE usuario_id=? ORDER BY id DESC LIMIT 1", (usuario_id,)).fetchone()
                valores = (plano, 'isenta' if isento else 'pendente', isento, motivo, inicio, fim,
                           session.get('usuario_id'), usuario_id)
                if assinatura:
                    cur.execute("""UPDATE assinaturas_saas
                        SET plano_codigo=?, status=?, isento=?, motivo_isencao=?, isencao_inicio=?,
                            isencao_fim=?, concedida_por=?, atualizado_em=CURRENT_TIMESTAMP
                        WHERE id=?""", valores[:-1] + (assinatura['id'],))
                else:
                    cur.execute("""INSERT INTO assinaturas_saas
                        (usuario_id, escola_id, plano_codigo, status, gateway, isento,
                         motivo_isencao, isencao_inicio, isencao_fim, concedida_por)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (usuario_id, escola_id, plano, 'isenta' if isento else 'pendente',
                         'cortesia' if isento else 'manual', isento, motivo, inicio, fim,
                         session.get('usuario_id')))
                db.commit()
                flash('Cortesia/assinatura atualizada com sucesso.', 'sucesso')
                return redirect(url_for('gestao_cortesias'))

            professores = cur.execute("""SELECT u.id, u.nome, u.email, u.plano_codigo,
                    u.assinatura_status, e.nome_instituicao,
                    a.id assinatura_id, COALESCE(a.isento,0) isento, a.motivo_isencao, a.isencao_inicio, a.isencao_fim
                FROM usuarios u
                LEFT JOIN escolas e ON e.id=u.escola_id
                LEFT JOIN assinaturas_saas a ON a.id=(SELECT a2.id FROM assinaturas_saas a2 WHERE a2.usuario_id=u.id ORDER BY a2.id DESC LIMIT 1)
                WHERE COALESCE(u.tipo_conta,'institucional')='autonomo'
                ORDER BY u.nome""").fetchall()
            instituicoes = cur.execute("""SELECT e.id, e.nome_instituicao, e.plano_codigo,
                    u.id usuario_id, a.id assinatura_id, COALESCE(a.isento,0) isento, a.motivo_isencao,
                    a.isencao_inicio, a.isencao_fim
                FROM escolas e
                LEFT JOIN usuarios u ON u.id=(SELECT u2.id FROM usuarios u2 LEFT JOIN cargos c2 ON c2.id=u2.cargo_id
                    WHERE u2.escola_id=e.id ORDER BY CASE WHEN c2.nome='Administrador da Instituição' THEN 0 ELSE 1 END, u2.id LIMIT 1)
                LEFT JOIN assinaturas_saas a ON a.id=(SELECT a2.id FROM assinaturas_saas a2 WHERE a2.usuario_id=u.id ORDER BY a2.id DESC LIMIT 1)
                WHERE COALESCE(e.workspace_autonomo,0)=0
                ORDER BY e.nome_instituicao""").fetchall()
            return render_template('saas/cortesias.html', professores=professores, instituicoes=instituicoes, hoje=date.today().isoformat())
        finally:
            db.close()

    @app.route('/minha-assinatura')
    def minha_assinatura():
        if not session.get('usuario_id'):
            return redirect(url_for('login'))
        info = assinatura_usuario(session['usuario_id'])
        db = conectar_banco(); db.row_factory = sqlite3.Row; cur = db.cursor()
        try:
            assinatura = cur.execute("""
                SELECT a.*, p.nome AS plano_nome, p.valor_mensal
                FROM assinaturas_saas a
                LEFT JOIN planos_saas p ON p.codigo=a.plano_codigo
                WHERE a.usuario_id=? OR (a.escola_id=? AND ?!='autonomo')
                ORDER BY CASE WHEN a.usuario_id=? THEN 0 ELSE 1 END, a.id DESC LIMIT 1
            """, (session['usuario_id'], info['escola_id'] if info else None, info['tipo_conta'] if info else None, session['usuario_id'])).fetchone()
            pagamentos = []
            if assinatura:
                pagamentos = cur.execute("""SELECT * FROM pagamentos_saas
                    WHERE assinatura_id=? ORDER BY id DESC LIMIT 12""", (assinatura['id'],)).fetchall()
            return render_template('saas/minha_assinatura.html', assinatura=assinatura or info, pagamentos=pagamentos)
        finally:
            db.close()

    @app.route('/gestao/cortesias/<int:assinatura_id>/excluir', methods=['POST'])
    def excluir_cortesia(assinatura_id):
        if session.get('usuario_cargo', '').strip() != 'Administrador Geral':
            flash('Apenas o Administrador Geral pode excluir cortesias.', 'erro')
            return redirect('/dashboard')
        db=conectar_banco(); db.row_factory=sqlite3.Row; cur=db.cursor()
        try:
            a=cur.execute('SELECT * FROM assinaturas_saas WHERE id=? AND COALESCE(isento,0)=1',(assinatura_id,)).fetchone()
            if not a:
                flash('Cortesia não encontrada.', 'erro')
                return redirect(url_for('gestao_cortesias'))
            status_padrao = 'ativa' if a['plano_codigo']=='gratuito' else 'pendente'
            cur.execute("""UPDATE assinaturas_saas SET isento=0, motivo_isencao=NULL,
                isencao_inicio=NULL, isencao_fim=NULL, status=?, gateway='manual', atualizado_em=CURRENT_TIMESTAMP
                WHERE id=?""", (status_padrao, assinatura_id))
            if a['escola_id']:
                cur.execute('UPDATE usuarios SET assinatura_status=? WHERE escola_id=?',(status_padrao,a['escola_id']))
            else:
                cur.execute('UPDATE usuarios SET assinatura_status=? WHERE id=?',(status_padrao,a['usuario_id']))
            db.commit(); flash('Cortesia excluída. A conta voltou para a cobrança normal.', 'sucesso')
        finally:
            db.close()
        return redirect(url_for('gestao_cortesias'))

    @app.before_request
    def proteger_saas_e_limites():
        # O logout precisa passar sem qualquer sincronização/recriação da sessão.
        # Essa verificação deve ocorrer antes de consultar usuario_id ou o banco.
        if request.endpoint == 'logout' or request.path.rstrip('/') in {'/logout', '/sair'}:
            return None

        if not session.get('usuario_id'):
            return None

        # Sincroniza a identidade da sessão com o registro atual do banco em
        # toda requisição autenticada. Assim, nome, cargo, tipo de conta e
        # escola nunca permanecem herdados de um login anterior.
        db_identidade = conectar_banco()
        db_identidade.row_factory = sqlite3.Row
        try:
            usuario_atual = db_identidade.execute("""
                SELECT u.id, u.nome, u.escola_id, u.tipo_conta, u.ativo,
                       c.nome AS cargo
                FROM usuarios u
                LEFT JOIN cargos c ON c.id = u.cargo_id
                WHERE u.id = ?
                LIMIT 1
            """, (session.get('usuario_id'),)).fetchone()
        finally:
            db_identidade.close()

        if not usuario_atual or int(usuario_atual['ativo'] or 0) != 1:
            session.clear()
            flash('Sua sessão expirou. Entre novamente.', 'aviso')
            return redirect(url_for('login'))

        tipo_conta_atual = (usuario_atual['tipo_conta'] or 'institucional').strip().lower()
        cargo_atual = (usuario_atual['cargo'] or '').strip()
        if tipo_conta_atual == 'autonomo':
            cargo_atual = 'Professor Autônomo'

        session['usuario_nome'] = usuario_atual['nome']
        session['usuario_cargo'] = cargo_atual
        session['tipo_conta'] = tipo_conta_atual
        session['escola_id'] = usuario_atual['escola_id']

        # Mantém o workspace do autônomo íntegro inclusive para contas antigas
        # e atualiza a sessão antes de qualquer rota de turma/aluno/prova.
        if session.get('usuario_cargo') == 'Professor Autônomo':
            workspace_id = garantir_workspace_autonomo(session['usuario_id'])
            if workspace_id:
                session['escola_id'] = workspace_id

        info = assinatura_usuario(session['usuario_id'])
        if not info:
            return None

        permitidos = {
            'login', 'logout', 'static', 'pagina_planos', 'site_planos',
            'cadastro_professor_autonomo', 'cadastro_concluido',
            'minha_assinatura', 'checkout_professor', 'checkout_aguardando',
            'asaas_webhook', 'site_inicio', 'site_recursos', 'site_professores',
            'site_instituicoes', 'site_contato', 'site_sobre', 'site_termos',
            'site_privacidade', 'gestao_cortesias'
        }
        if not assinatura_ativa(info) and request.endpoint not in permitidos:
            flash('Sua assinatura não está ativa. Regularize o pagamento para liberar a plataforma.', 'erro')
            return redirect(url_for('minha_assinatura'))

        # O professor autônomo administra somente o próprio espaço.
        if info['tipo_conta'] == 'autonomo':
            path = request.path.rstrip('/')
            if path.startswith('/usuarios') or path.startswith('/gestao/instituicoes') or path.startswith('/permissoes'):
                flash('Essa área é exclusiva para contas institucionais.', 'erro')
                return redirect('/dashboard')

        if request.method != 'POST':
            # Recursos premium acessados por GET.
            if request.endpoint == 'corrigir_discursivas_aplicacao' and not possui_recurso(info, 'correcao_discursivas'):
                flash('A correção de discursivas está disponível no Plano Professor.', 'erro')
                return redirect(url_for('site_planos'))
            return None

        limites_por_endpoint = {
            'cadastrar_turma': 'turmas',
            'cadastrar_aluno': 'alunos',
            'gerar_prova': 'provas_mes',
            'gerar_prova_manual': 'provas_mes',
            'duplicar_prova': 'provas_mes',
        }
        tipo_limite = limites_por_endpoint.get(request.endpoint)
        if tipo_limite:
            permitido, mensagem = verificar_limite(conectar_banco, info, tipo_limite)
            if not permitido:
                flash(mensagem, 'erro')
                return redirect(url_for('site_planos'))

        # Impede criação/edição de questões discursivas no plano gratuito.
        if request.endpoint in {'cadastrar_questao', 'editar_questao'}:
            tipo_questao = (request.form.get('tipo_questao') or '').strip().lower()
            if tipo_questao in {'discursiva', 'resposta_curta', 'numerica'} and not possui_recurso(info, 'questoes_discursivas'):
                flash('Questões discursivas e abertas estão disponíveis no Plano Professor.', 'erro')
                return redirect(url_for('site_planos'))

        if request.endpoint == 'corrigir_discursivas_aplicacao' and not possui_recurso(info, 'correcao_discursivas'):
            flash('A correção de discursivas está disponível no Plano Professor.', 'erro')
            return redirect(url_for('site_planos'))

        return None
