"""Módulo Horários v1 refeito sem dependências do módulo anterior."""
import sqlite3
from flask import Blueprint, session, request, redirect, url_for, render_template, flash, jsonify, abort

bp = Blueprint('horarios_novo', __name__, url_prefix='/horarios')
_connect = None

def init_horarios(app, conectar):
    global _connect
    _connect = conectar
    app.register_blueprint(bp)

def db():
    con = _connect(); con.row_factory = sqlite3.Row
    con.executescript('''
    CREATE TABLE IF NOT EXISTS horario_periodos (
      id INTEGER PRIMARY KEY AUTOINCREMENT, escola_id INTEGER NOT NULL, turno TEXT NOT NULL,
      dia INTEGER NOT NULL, ordem INTEGER NOT NULL, inicio TEXT NOT NULL, fim TEXT NOT NULL,
      tipo TEXT NOT NULL DEFAULT 'aula', UNIQUE(escola_id,turno,dia,ordem));
    CREATE TABLE IF NOT EXISTS horario_matriz (
      escola_id INTEGER NOT NULL, turma_id INTEGER NOT NULL, componente_id INTEGER NOT NULL,
      aulas_semanais INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(escola_id,turma_id,componente_id));
    ''')
    # A coluna periodo_id não pode ser globalmente UNIQUE: cada período atende várias turmas.
    # Em bancos novos e antigos, usamos tabela corrigida separada para evitar migração destrutiva.
    con.execute('''CREATE TABLE IF NOT EXISTS horario_grade (
       escola_id INTEGER NOT NULL, turma_id INTEGER NOT NULL, periodo_id INTEGER NOT NULL,
       componente_id INTEGER NOT NULL, professor_id INTEGER NOT NULL,
       PRIMARY KEY(escola_id,turma_id,periodo_id))''')
    return con

def scope(con, escola_param=None):
    uid = session.get('usuario_id'); cargo = (session.get('usuario_cargo') or '').strip()
    if not uid: abort(401)
    if cargo == 'Administrador Geral':
        escola = escola_param or session.get('escola_id')
        if not escola: return None, cargo
    else:
        row = con.execute('SELECT escola_id FROM usuarios WHERE id=?', (uid,)).fetchone()
        escola = row['escola_id'] if row else None
    if not escola: abort(403)
    return int(escola), cargo

def editable(cargo):
    return cargo in ('Administrador Geral','Administrador da Instituição','Coordenador','Professor Autônomo')

def professor_only(cargo):
    return cargo == 'Professor'

def turnos(con, escola):
    return [r['turno'] for r in con.execute('SELECT DISTINCT turno FROM turmas WHERE escola_id=? ORDER BY turno',(escola,))]

def turmas(con, escola, cargo):
    if professor_only(cargo):
        return con.execute('''SELECT DISTINCT t.* FROM turmas t JOIN professor_vinculos pv ON pv.turma_id=t.id
            WHERE t.escola_id=? AND pv.professor_id=? ORDER BY t.nome''',(escola,session['usuario_id'])).fetchall()
    return con.execute('SELECT * FROM turmas WHERE escola_id=? ORDER BY nome',(escola,)).fetchall()

def nome_turma(t):
    # Evita acrescentar o ano a um nome que já o contém.
    return t['nome']

@bp.route('/', methods=['GET','POST'])
def index():
    con=db()
    try:
        escola,cargo=scope(con,request.values.get('escola_id'))
        if escola is None:
            escolas=con.execute('SELECT id,nome_instituicao FROM escolas ORDER BY nome_instituicao').fetchall()
            return render_template('horarios_novo/escolher.html',escolas=escolas)
        if request.method=='POST':
            if not editable(cargo): abort(403)
            turno=request.form.get('turno','').strip()
            if turno not in turnos(con,escola): abort(400)
            dias=[1,2,3,4,5]
            aulas=int(request.form.get('aulas',5))
            duracao=int(request.form.get('duracao',50))
            intervalo=int(request.form.get('intervalo',15))
            apos=int(request.form.get('apos',3))
            inicio=request.form.get('inicio','07:00')
            if not (1<=aulas<=12 and 20<=duracao<=120 and 0<=intervalo<=90 and 0<=apos<=aulas): abort(400)
            from datetime import datetime,timedelta
            try: base=datetime.strptime(inicio,'%H:%M')
            except ValueError: abort(400)
            for dia in dias:
                con.execute('DELETE FROM horario_periodos WHERE escola_id=? AND turno=? AND dia=? AND id NOT IN (SELECT periodo_id FROM horario_grade)',(escola,turno,dia))
                # Não altera períodos que já possuam aulas: requer limpar grade primeiro.
                existentes=con.execute('SELECT COUNT(*) AS n FROM horario_grade g JOIN horario_periodos p ON p.id=g.periodo_id WHERE g.escola_id=? AND p.turno=? AND p.dia=?',(escola,turno,dia)).fetchone()['n']
                if existentes:
                    con.rollback(); flash('Há aulas distribuídas neste turno. Remova-as antes de alterar os períodos.','erro')
                    return redirect(url_for('.index',escola_id=escola))
                con.execute('DELETE FROM horario_periodos WHERE escola_id=? AND turno=? AND dia=?',(escola,turno,dia))
                atual=base; ordem=1
                for i in range(1,aulas+1):
                    fim=atual+timedelta(minutes=duracao)
                    con.execute('INSERT INTO horario_periodos(escola_id,turno,dia,ordem,inicio,fim,tipo) VALUES(?,?,?,?,?,?,?)',(escola,turno,dia,ordem,atual.strftime('%H:%M'),fim.strftime('%H:%M'),'aula'))
                    atual=fim; ordem+=1
                    if i==apos and intervalo:
                        fim=atual+timedelta(minutes=intervalo)
                        con.execute('INSERT INTO horario_periodos(escola_id,turno,dia,ordem,inicio,fim,tipo) VALUES(?,?,?,?,?,?,?)',(escola,turno,dia,ordem,atual.strftime('%H:%M'),fim.strftime('%H:%M'),'intervalo'))
                        atual=fim; ordem+=1
            con.commit(); flash('Configuração do turno salva.','sucesso')
            return redirect(url_for('.index',escola_id=escola))
        periodos=con.execute('SELECT * FROM horario_periodos WHERE escola_id=? AND dia=1 ORDER BY turno,ordem',(escola,)).fetchall()
        return render_template('horarios_novo/index.html',escola=escola,cargo=cargo,editavel=editable(cargo),turnos=turnos(con,escola),periodos=periodos)
    finally: con.close()

@bp.route('/matriz',methods=['GET','POST'])
def matriz():
    con=db()
    try:
        escola,cargo=scope(con,request.values.get('escola_id'))
        if escola is None: return redirect(url_for('.index'))
        if not editable(cargo): abort(403)
        turma_id=request.values.get('turma_id',type=int)
        lista=turmas(con,escola,cargo)
        ids={t['id'] for t in lista}
        if turma_id not in ids: turma_id=next(iter(ids),None)
        if request.method=='POST':
            cid=request.form.get('componente_id',type=int); qtd=request.form.get('aulas_semanais',type=int)
            if turma_id not in ids or qtd is None or not 0<=qtd<=40: abort(400)
            valido=con.execute('SELECT 1 FROM componentes_curriculares WHERE id=? AND escola_id=?',(cid,escola)).fetchone()
            if not valido: abort(400)
            con.execute('INSERT INTO horario_matriz(escola_id,turma_id,componente_id,aulas_semanais) VALUES(?,?,?,?) ON CONFLICT(escola_id,turma_id,componente_id) DO UPDATE SET aulas_semanais=excluded.aulas_semanais',(escola,turma_id,cid,qtd))
            con.commit(); flash('Carga horária atualizada.','sucesso')
            return redirect(url_for('.matriz',escola_id=escola,turma_id=turma_id))
        comps=con.execute('''SELECT c.id,c.nome,COALESCE(m.aulas_semanais,0) AS aulas FROM componentes_curriculares c LEFT JOIN horario_matriz m ON m.componente_id=c.id AND m.escola_id=? AND m.turma_id=? WHERE c.escola_id=? AND c.ativo=1 ORDER BY c.nome''',(escola,turma_id,escola)).fetchall()
        return render_template('horarios_novo/matriz.html',escola=escola,turmas=lista,turma_id=turma_id,componentes=comps)
    finally: con.close()

@bp.route('/editor')
def editor():
    con=db()
    try:
        escola,cargo=scope(con,request.args.get('escola_id'))
        if escola is None: return redirect(url_for('.index'))
        turno=request.args.get('turno') or (turnos(con,escola) or [''])[0]
        grade=con.execute('SELECT * FROM horario_periodos WHERE escola_id=? AND turno=? ORDER BY dia,ordem',(escola,turno)).fetchall()
        ts=[dict(t,rotulo=nome_turma(t)) for t in turmas(con,escola,cargo) if t['turno']==turno]
        vinculados=con.execute('''SELECT DISTINCT pv.professor_id,pv.turma_id,pv.componente_id,u.nome AS professor,c.nome AS componente
          FROM professor_vinculos pv JOIN usuarios u ON u.id=pv.professor_id
          JOIN componentes_curriculares c ON c.id=pv.componente_id
          JOIN turmas t ON t.id=pv.turma_id
          WHERE t.escola_id=? AND t.turno=? AND c.escola_id=? ORDER BY c.nome,u.nome''',(escola,turno,escola)).fetchall()
        if professor_only(cargo): vinculados=[v for v in vinculados if v['professor_id']==session['usuario_id']]
        matriz=con.execute('SELECT turma_id,componente_id,aulas_semanais FROM horario_matriz WHERE escola_id=?',(escola,)).fetchall()
        aloc=con.execute('''SELECT g.*,p.dia,p.ordem,c.nome AS componente,u.nome AS professor FROM horario_grade g
          JOIN horario_periodos p ON p.id=g.periodo_id JOIN componentes_curriculares c ON c.id=g.componente_id
          JOIN usuarios u ON u.id=g.professor_id WHERE g.escola_id=? AND p.turno=?''',(escola,turno)).fetchall()
        if professor_only(cargo): aloc=[a for a in aloc if a['professor_id']==session['usuario_id']]
        return render_template('horarios_novo/editor.html',escola=escola,cargo=cargo,editavel=editable(cargo),turno=turno,turnos=turnos(con,escola),turmas=ts,periodos=[dict(p) for p in grade],vinculos=[dict(v) for v in vinculados],matriz=[dict(m) for m in matriz],alocacoes=[dict(a) for a in aloc])
    finally: con.close()

@bp.route('/alocar',methods=['POST'])
def alocar():
    con=db()
    try:
        escola,cargo=scope(con,request.form.get('escola_id'))
        if not editable(cargo): abort(403)
        tid=request.form.get('turma_id',type=int); pid=request.form.get('periodo_id',type=int)
        cid=request.form.get('componente_id',type=int); uid=request.form.get('professor_id',type=int)
        if not all([tid,pid]): return jsonify(erro='Dados incompletos'),400
        periodo=con.execute('SELECT * FROM horario_periodos WHERE id=? AND escola_id=? AND tipo=?',(pid,escola,'aula')).fetchone()
        turma=con.execute('SELECT * FROM turmas WHERE id=? AND escola_id=?',(tid,escola)).fetchone()
        if not periodo or not turma or turma['turno']!=periodo['turno']: return jsonify(erro='Turma ou período inválido'),400
        if not cid or not uid:
            con.execute('DELETE FROM horario_grade WHERE escola_id=? AND turma_id=? AND periodo_id=?',(escola,tid,pid));con.commit()
            return jsonify(ok=True)
        vinculo=con.execute('''SELECT 1 FROM professor_vinculos pv JOIN componentes_curriculares c ON c.id=pv.componente_id WHERE pv.professor_id=? AND pv.turma_id=? AND pv.componente_id=? AND c.escola_id=?''',(uid,tid,cid,escola)).fetchone()
        if not vinculo: return jsonify(erro='Professor sem vínculo com esta turma e disciplina'),400
        carga=con.execute('SELECT aulas_semanais FROM horario_matriz WHERE escola_id=? AND turma_id=? AND componente_id=?',(escola,tid,cid)).fetchone()
        if not carga or carga['aulas_semanais']<=0: return jsonify(erro='Configure primeiro a carga semanal na matriz'),400
        ocupadas=con.execute('SELECT COUNT(*) AS n FROM horario_grade WHERE escola_id=? AND turma_id=? AND componente_id=? AND periodo_id<>?',(escola,tid,cid,pid)).fetchone()['n']
        if ocupadas>=carga['aulas_semanais']: return jsonify(erro='Carga semanal desta disciplina já foi preenchida'),409
        conflito=con.execute('''SELECT 1 FROM horario_grade g JOIN horario_periodos p ON p.id=g.periodo_id WHERE g.professor_id=? AND g.periodo_id<>? AND p.dia=? AND p.inicio<? AND p.fim>? LIMIT 1''',(uid,pid,periodo['dia'],periodo['fim'],periodo['inicio'])).fetchone()
        if conflito: return jsonify(erro='Professor já possui aula neste horário'),409
        con.execute('''INSERT INTO horario_grade(escola_id,turma_id,periodo_id,componente_id,professor_id) VALUES(?,?,?,?,?) ON CONFLICT(escola_id,turma_id,periodo_id) DO UPDATE SET componente_id=excluded.componente_id,professor_id=excluded.professor_id''',(escola,tid,pid,cid,uid))
        con.commit(); return jsonify(ok=True)
    finally: con.close()
