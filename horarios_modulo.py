"""Horários escolares da ARK EDUS: acesso isolado por instituição e professor."""
import sqlite3
import re
from flask import Blueprint, request, session, redirect, render_template, flash, abort

horarios_bp = Blueprint('horarios', __name__, url_prefix='/horarios')
GESTORES = {'Administrador Geral', 'Administrador da Instituição', 'Coordenador', 'Professor Autônomo'}

def instalar(conectar):
    db = conectar()
    db.executescript('''
    CREATE TABLE IF NOT EXISTS horarios_periodos (
      id INTEGER PRIMARY KEY AUTOINCREMENT, escola_id INTEGER NOT NULL,
      turno TEXT NOT NULL, numero INTEGER NOT NULL,
      inicio TEXT NOT NULL, fim TEXT NOT NULL,
      UNIQUE(escola_id, turno, numero));
    CREATE TABLE IF NOT EXISTS horarios_aulas (
      id INTEGER PRIMARY KEY AUTOINCREMENT, escola_id INTEGER NOT NULL,
      turma_id INTEGER NOT NULL, dia INTEGER NOT NULL CHECK(dia BETWEEN 0 AND 6),
      periodo_id INTEGER NOT NULL, professor_id INTEGER NOT NULL,
      componente TEXT NOT NULL, ano_letivo INTEGER NOT NULL,
      UNIQUE(turma_id, dia, periodo_id, ano_letivo));
    CREATE INDEX IF NOT EXISTS idx_horarios_prof ON horarios_aulas(professor_id,ano_letivo,dia);
    ''')
    db.executescript("""CREATE TABLE IF NOT EXISTS horarios_intervalos (id INTEGER PRIMARY KEY AUTOINCREMENT, escola_id INTEGER NOT NULL, turno TEXT NOT NULL, nome TEXT NOT NULL, inicio TEXT NOT NULL, fim TEXT NOT NULL); CREATE TABLE IF NOT EXISTS horarios_cargas (escola_id INTEGER NOT NULL, turma_id INTEGER NOT NULL, componente_id INTEGER NOT NULL, professor_id INTEGER NOT NULL, quantidade INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(escola_id,turma_id,componente_id,professor_id));""")
    db.commit(); db.close()

def registrar(app, conectar, obter_escola):
    instalar(conectar)
    def contexto():
        if not session.get('usuario_id'):
            abort(401)
        cargo = session.get('usuario_cargo', '').strip()
        if cargo not in GESTORES | {'Professor'}:
            abort(403)
        escola = obter_escola()
        if cargo != 'Administrador Geral' and not escola:
            abort(403)
        return cargo, int(escola) if escola else None, int(session['usuario_id'])

    def vinculos(db, escola, usuario=None):
        # O cadastro de usuários utiliza professor_vinculos; professor_componentes
        # permanece como fonte legada para instalações anteriores.
        sql = """SELECT DISTINCT v.professor_id, v.turma_id, v.componente_id,
                        c.nome AS componente, u.nome AS professor_nome
                 FROM (
                   SELECT professor_id,turma_id,componente_id FROM professor_vinculos
                   UNION
                   SELECT professor_id,turma_id,componente_id FROM professor_componentes
                 ) v
                 JOIN turmas t ON t.id=v.turma_id AND t.escola_id=?
                 JOIN componentes_curriculares c ON c.id=v.componente_id
                      AND c.escola_id=? AND c.ativo=1
                 JOIN usuarios u ON u.id=v.professor_id AND u.ativo=1
                 WHERE 1=1"""
        args=[escola,escola]
        if usuario is not None:
            sql+=' AND v.professor_id=?'; args.append(usuario)
        return [dict(x) for x in db.execute(sql,args).fetchall()]

    @horarios_bp.route('/configurar', methods=['POST'])
    def configurar():
        cargo,escola,usuario=contexto()
        if cargo not in GESTORES:abort(403)
        if cargo=='Administrador Geral':escola=request.form.get('escola_id',type=int) or escola
        turno=request.form.get('turno','')
        if turno not in ('Matutino','Vespertino','Noturno','Integral') or not escola:abort(400)
        total=request.form.get('total',type=int)
        if total is None or not 1<=total<=10:abort(400)
        db=conectar();db.row_factory=sqlite3.Row
        for n in range(1,total+1):
            inicio=request.form.get(f'inicio_{n}','');fim=request.form.get(f'fim_{n}','')
            if not re.fullmatch(r'\d{2}:\d{2}',inicio) or not re.fullmatch(r'\d{2}:\d{2}',fim) or inicio>=fim:db.close();abort(400)
            db.execute('INSERT INTO horarios_periodos(escola_id,turno,numero,inicio,fim) VALUES(?,?,?,?,?) ON CONFLICT(escola_id,turno,numero) DO UPDATE SET inicio=excluded.inicio,fim=excluded.fim',(escola,turno,n,inicio,fim))
        db.execute('DELETE FROM horarios_periodos WHERE escola_id=? AND turno=? AND numero>? AND id NOT IN (SELECT periodo_id FROM horarios_aulas)',(escola,turno,total))
        db.execute('DELETE FROM horarios_intervalos WHERE escola_id=? AND turno=?',(escola,turno))
        for n in range(1,4):
            inicio=request.form.get(f'intervalo_inicio_{n}','');fim=request.form.get(f'intervalo_fim_{n}','')
            if inicio and fim:
                if inicio>=fim:db.close();abort(400)
                db.execute('INSERT INTO horarios_intervalos(escola_id,turno,nome,inicio,fim) VALUES(?,?,?,?,?)',(escola,turno,request.form.get(f'intervalo_nome_{n}') or 'Intervalo',inicio,fim))
        db.commit();db.close();flash('Configuração do turno salva.','success')
        return redirect('/horarios/?escola_id='+str(escola))

    @horarios_bp.route('/cargas', methods=['POST'])
    def cargas():
        cargo,escola,usuario=contexto()
        if cargo not in GESTORES:abort(403)
        if cargo=='Administrador Geral':escola=request.form.get('escola_id',type=int) or escola
        turma=request.form.get('turma_id',type=int);prof=request.form.get('professor_id',type=int);comp=request.form.get('componente_id',type=int);quant=request.form.get('quantidade',type=int)
        if not all((escola,turma,prof,comp)) or quant is None or not 0<=quant<=30:abort(400)
        db=conectar();db.row_factory=sqlite3.Row
        if not any(v['turma_id']==turma and v['professor_id']==prof and v['componente_id']==comp for v in vinculos(db,escola,usuario if cargo=='Professor Autônomo' else None)):db.close();abort(403)
        if cargo=='Coordenador' and not db.execute('SELECT 1 FROM coordenador_turmas WHERE usuario_id=? AND turma_id=?',(usuario,turma)).fetchone():db.close();abort(403)
        db.execute('INSERT INTO horarios_cargas VALUES(?,?,?,?,?) ON CONFLICT(escola_id,turma_id,componente_id,professor_id) DO UPDATE SET quantidade=excluded.quantidade',(escola,turma,comp,prof,quant))
        db.commit();db.close();flash('Carga horária salva.','success');return redirect('/horarios/?escola_id='+str(escola))

    @horarios_bp.route('/', methods=['GET'])
    def index():
        cargo, escola, usuario = contexto()
        gestor = cargo in GESTORES
        db = conectar(); db.row_factory = sqlite3.Row
        escolas = db.execute('SELECT id,nome_instituicao FROM escolas ORDER BY nome_instituicao').fetchall() if cargo == 'Administrador Geral' else []
        if cargo == 'Administrador Geral':
            escola = request.args.get('escola_id', type=int) or escola
        if not escola:
            db.close(); return render_template('horarios/index.html', gestor=gestor, escolas=escolas, escola=None, turmas=[], professores=[], periodos=[], aulas=[], vinculos=[], cargas=[], intervalos=[], ano=2026, selecionada=None)
        ano = request.args.get('ano', type=int) or int(session.get('ano_letivo_visualizado') or 2026)
        turmas = db.execute('SELECT id,nome,ano,turno FROM turmas WHERE escola_id=? ORDER BY ano,nome',(escola,)).fetchall()
        professores = db.execute("SELECT DISTINCT u.id,u.nome FROM usuarios u JOIN (SELECT professor_id,turma_id FROM professor_vinculos UNION SELECT professor_id,turma_id FROM professor_componentes) v ON v.professor_id=u.id JOIN turmas t ON t.id=v.turma_id WHERE t.escola_id=? AND u.ativo=1 ORDER BY u.nome",(escola,)).fetchall()
        periodos = db.execute('SELECT * FROM horarios_periodos WHERE escola_id=? ORDER BY turno,numero',(escola,)).fetchall()
        turma_id = request.args.get('turma_id', type=int)
        if not turma_id and gestor and turmas: turma_id = turmas[0]['id']
        if turma_id and not any(t['id']==turma_id for t in turmas): db.close();abort(403)
        if cargo == 'Coordenador':
            allowed = {r['turma_id'] for r in db.execute('SELECT turma_id FROM coordenador_turmas WHERE usuario_id=?',(usuario,))}
            turmas = [t for t in turmas if t['id'] in allowed]
            if turma_id and turma_id not in allowed: turma_id = None
        sql = '''SELECT a.*,t.nome AS turma_nome,t.ano AS turma_ano,t.turno,p.numero,p.inicio,p.fim,u.nome AS professor_nome
                 FROM horarios_aulas a JOIN turmas t ON t.id=a.turma_id
                 JOIN horarios_periodos p ON p.id=a.periodo_id JOIN usuarios u ON u.id=a.professor_id
                 WHERE a.escola_id=? AND a.ano_letivo=?'''
        params = [escola,ano]
        if not gestor:
            sql += ' AND a.professor_id=?'; params.append(usuario)
        elif turma_id:
            sql += ' AND a.turma_id=?'; params.append(turma_id)
        if cargo == 'Coordenador':
            sql += ' AND a.turma_id IN (SELECT turma_id FROM coordenador_turmas WHERE usuario_id=?)'; params.append(usuario)
        if cargo == 'Professor Autônomo':
            sql += ' AND a.professor_id=?'; params.append(usuario)
        sql += ' ORDER BY a.dia,p.inicio'
        aulas = db.execute(sql,params).fetchall()
        vinculos=vinculos(db,escola,usuario if cargo=='Professor Autônomo' else None) if gestor else []
        if cargo=='Coordenador':vinculos=[v for v in vinculos if v['turma_id'] in {t['id'] for t in turmas}]
        cargas=[dict(r) for r in db.execute('SELECT * FROM horarios_cargas WHERE escola_id=?',(escola,)).fetchall()]
        intervalos=db.execute('SELECT * FROM horarios_intervalos WHERE escola_id=? ORDER BY turno,inicio',(escola,)).fetchall()
        db.close()
        return render_template('horarios/index.html',vinculos=vinculos,cargas=cargas,intervalos=intervalos,gestor=gestor,escolas=escolas,escola=escola,turmas=turmas,professores=professores,periodos=periodos,aulas=aulas,ano=ano,selecionada=turma_id)

    @horarios_bp.route('/periodos',methods=['POST'])
    def periodo():
        cargo,escola,_ = contexto()
        if cargo not in GESTORES: abort(403)
        if cargo=='Administrador Geral': escola=request.form.get('escola_id',type=int) or escola
        if not escola: abort(400)
        turno=request.form.get('turno','').strip()
        numero=request.form.get('numero',type=int)
        inicio=request.form.get('inicio',''); fim=request.form.get('fim','')
        if turno not in ('Matutino','Vespertino','Noturno','Integral') or not numero or numero>20 or not inicio or not fim or inicio>=fim: abort(400)
        db=conectar()
        db.execute('INSERT INTO horarios_periodos(escola_id,turno,numero,inicio,fim) VALUES(?,?,?,?,?) ON CONFLICT(escola_id,turno,numero) DO UPDATE SET inicio=excluded.inicio,fim=excluded.fim',(escola,turno,numero,inicio,fim))
        db.commit();db.close();flash('Período salvo.','success')
        return redirect('/horarios/?escola_id='+str(escola))

    @horarios_bp.route('/aulas',methods=['POST'])
    def aula():
        cargo,escola,usuario=contexto()
        if cargo not in GESTORES: abort(403)
        if cargo=='Administrador Geral': escola=request.form.get('escola_id',type=int) or escola
        turma=request.form.get('turma_id',type=int); periodo=request.form.get('periodo_id',type=int)
        professor=request.form.get('professor_id',type=int); dia=request.form.get('dia',type=int)
        ano=request.form.get('ano',type=int); componente=request.form.get('componente','').strip()[:120]
        if not escola or not all([turma,periodo,professor,ano,componente]) or dia not in range(7): abort(400)
        db=conectar();db.row_factory=sqlite3.Row
        t=db.execute('SELECT * FROM turmas WHERE id=? AND escola_id=?',(turma,escola)).fetchone()
        p=db.execute('SELECT * FROM horarios_periodos WHERE id=? AND escola_id=?',(periodo,escola)).fetchone()
        u=db.execute('SELECT id FROM usuarios WHERE id=? AND escola_id=?',(professor,escola)).fetchone()
        if not t or not p or not u or t['turno'].lower()!=p['turno'].lower(): db.close();abort(400)
        if cargo=='Professor Autônomo' and professor!=usuario: db.close();abort(403)
        if not any(v['turma_id']==turma and v['professor_id']==professor and v['componente'].casefold()==componente.casefold() for v in vinculos(db,escola)):
            db.close();flash('A disciplina e o professor devem estar vinculados a esta turma no cadastro de usuários.','error');return redirect('/horarios/painel?escola_id='+str(escola)+'&ano='+str(ano))

        if cargo=='Coordenador':
            permitido=db.execute('SELECT 1 FROM coordenador_turmas WHERE usuario_id=? AND turma_id=?',(usuario,turma)).fetchone()
            if not permitido: db.close();abort(403)
        choque=db.execute('''SELECT 1 FROM horarios_aulas a JOIN horarios_periodos x ON x.id=a.periodo_id
          WHERE a.professor_id=? AND a.ano_letivo=? AND a.dia=? AND x.inicio < ? AND x.fim > ? AND NOT(a.turma_id=? AND a.periodo_id=?)''',
          (professor,ano,dia,p['fim'],p['inicio'],turma,periodo)).fetchone()
        if choque: db.close();flash('Conflito: professor já possui aula nesse horário.','error');return redirect('/horarios/?turma_id='+str(turma)+'&escola_id='+str(escola))
        db.execute('''INSERT INTO horarios_aulas(escola_id,turma_id,dia,periodo_id,professor_id,componente,ano_letivo)
           VALUES(?,?,?,?,?,?,?) ON CONFLICT(turma_id,dia,periodo_id,ano_letivo)
           DO UPDATE SET professor_id=excluded.professor_id,componente=excluded.componente''',
           (escola,turma,dia,periodo,professor,componente,ano))
        db.commit();db.close();flash('Aula salva.','success')
        return redirect('/horarios/painel?escola_id='+str(escola)+'&ano='+str(ano)) if request.form.get('return_to')=='painel' else redirect('/horarios/?turma_id='+str(turma)+'&escola_id='+str(escola)+'&ano='+str(ano))

    @horarios_bp.route('/aulas/<int:aula_id>/excluir',methods=['POST'])
    def excluir(aula_id):
        cargo,escola,usuario=contexto()
        if cargo not in GESTORES: abort(403)
        db=conectar();db.row_factory=sqlite3.Row
        a=db.execute('SELECT * FROM horarios_aulas WHERE id=?',(aula_id,)).fetchone()
        if not a: db.close();abort(404)
        if cargo!='Administrador Geral' and a['escola_id']!=escola: db.close();abort(403)
        if cargo=='Professor Autônomo' and a['professor_id']!=usuario: db.close();abort(403)
        if cargo=='Coordenador' and not db.execute('SELECT 1 FROM coordenador_turmas WHERE usuario_id=? AND turma_id=?',(usuario,a['turma_id'])).fetchone():db.close();abort(403)
        db.execute('DELETE FROM horarios_aulas WHERE id=?',(aula_id,));db.commit();db.close()
        flash('Aula removida.','success');return redirect('/horarios/?turma_id='+str(a['turma_id'])+'&escola_id='+str(a['escola_id']))

    @horarios_bp.route('/painel')
    def painel():
        cargo, escola, usuario = contexto()
        gestor = cargo in GESTORES
        db = conectar(); db.row_factory = sqlite3.Row
        escolas = db.execute('SELECT id,nome_instituicao FROM escolas ORDER BY nome_instituicao').fetchall() if cargo == 'Administrador Geral' else []
        if cargo == 'Administrador Geral':
            escola = request.args.get('escola_id', type=int) or escola
        ano = request.args.get('ano', type=int) or int(session.get('ano_letivo_visualizado') or 2026)
        turmas, periodos, aulas, professores, vinculacoes, cargas, intervalos = [], [], [], [], [], [], []
        if escola:
            turmas = db.execute('SELECT id,nome,ano,turno FROM turmas WHERE escola_id=? ORDER BY ano,nome',(escola,)).fetchall()
            if cargo == 'Coordenador':
                turmas = [t for t in turmas if db.execute('SELECT 1 FROM coordenador_turmas WHERE usuario_id=? AND turma_id=?',(usuario,t['id'])).fetchone()]
            periodos = db.execute('SELECT * FROM horarios_periodos WHERE escola_id=? ORDER BY turno,numero',(escola,)).fetchall()
            professores = db.execute('SELECT DISTINCT u.id,u.nome FROM usuarios u JOIN (SELECT professor_id,turma_id FROM professor_vinculos UNION SELECT professor_id,turma_id FROM professor_componentes) v ON v.professor_id=u.id JOIN turmas t ON t.id=v.turma_id WHERE t.escola_id=? AND u.ativo=1 ORDER BY u.nome',(escola,)).fetchall()
            vinculacoes=vinculos(db,escola,usuario if cargo in ('Professor','Professor Autônomo') else None)
            if cargo=='Coordenador':vinculacoes=[v for v in vinculacoes if v['turma_id'] in {t['id'] for t in turmas}]
            if cargo in ('Professor','Professor Autônomo'):
                turmas=[t for t in turmas if t['id'] in {v['turma_id'] for v in vinculacoes}]
            cargas=[dict(r) for r in db.execute('SELECT * FROM horarios_cargas WHERE escola_id=?',(escola,)).fetchall()]
            intervalos=[dict(r) for r in db.execute('SELECT * FROM horarios_intervalos WHERE escola_id=?',(escola,)).fetchall()]
            sql = """SELECT a.*,p.numero,p.turno AS periodo_turno,p.inicio,p.fim,u.nome AS professor_nome
                     FROM horarios_aulas a JOIN horarios_periodos p ON p.id=a.periodo_id
                     JOIN usuarios u ON u.id=a.professor_id WHERE a.escola_id=? AND a.ano_letivo=?"""
            params = [escola,ano]
            if cargo in ('Professor','Professor Autônomo'):
                sql += ' AND a.professor_id=?';params.append(usuario)
            if cargo == 'Coordenador':
                allowed=[t['id'] for t in turmas]
                if not allowed: sql+=' AND 1=0'
                else:
                    sql+=' AND a.turma_id IN ('+','.join('?' for _ in allowed)+')';params.extend(allowed)
            aulas = db.execute(sql,params).fetchall()
        db.close()
        return render_template('horarios/painel.html',gestor=gestor,escolas=escolas,escola=escola,
                               turmas=turmas,periodos=periodos,aulas=aulas,professores=professores,ano=ano,vinculacoes=vinculacoes,cargas=cargas,intervalos=intervalos)

    app.register_blueprint(horarios_bp)
