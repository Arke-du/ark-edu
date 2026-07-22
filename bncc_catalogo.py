import sqlite3
import unicodedata
from typing import Any


def _normalizar(texto: str | None) -> str:
    texto = str(texto or "")
    texto = texto.replace("\u200b", " ").replace("\ufeff", " ")
    texto = texto.replace("–", "-").replace("—", "-")
    texto = " ".join(texto.split()).strip().casefold()
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caractere) != "Mn"
    )


ALIASES_COMPONENTES = {
    'ingles': 'Língua Inglesa',
    'lingua inglesa': 'Língua Inglesa',
    'portugues': 'Língua Portuguesa',
    'lingua portuguesa': 'Língua Portuguesa',
    'educacao fisica': 'Educação Física',
    'ensino religioso': 'Ensino Religioso',
    'ciencias': 'Ciências',
    'matematica': 'Matemática',
    'historia': 'História',
    'geografia': 'Geografia',
    'arte': 'Arte',
    'biologia': 'Ciências da Natureza e suas Tecnologias',
    'fisica': 'Ciências da Natureza e suas Tecnologias',
    'quimica': 'Ciências da Natureza e suas Tecnologias',
    'filosofia': 'Ciências Humanas e Sociais Aplicadas',
    'sociologia': 'Ciências Humanas e Sociais Aplicadas',
}


def componente_catalogo(componente: str, etapa: str) -> str:
    nome = ALIASES_COMPONENTES.get(_normalizar(componente), componente.strip())
    if _normalizar(etapa) == 'ensino medio':
        n = _normalizar(componente)
        if n in {'arte', 'educacao fisica', 'ingles', 'lingua inglesa'}:
            return 'Linguagens e suas Tecnologias'
        if n in {'biologia', 'fisica', 'quimica', 'ciencias da natureza e suas tecnologias'}:
            return 'Ciências da Natureza e suas Tecnologias'
        if n in {'historia', 'geografia', 'filosofia', 'sociologia', 'ciencias humanas e sociais aplicadas'}:
            return 'Ciências Humanas e Sociais Aplicadas'
        if n in {'matematica', 'matematica e suas tecnologias'}:
            return 'Matemática e suas Tecnologias'
    return nome


def garantir_tabela_bncc(db_path: str) -> None:
    banco = sqlite3.connect(db_path)
    try:
        banco.executescript('''
        CREATE TABLE IF NOT EXISTS bncc_habilidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            etapa_ensino TEXT NOT NULL,
            ano_serie TEXT NOT NULL DEFAULT '',
            area_conhecimento TEXT NOT NULL DEFAULT '',
            componente TEXT NOT NULL,
            unidade_tematica TEXT NOT NULL DEFAULT '',
            objeto_conhecimento TEXT NOT NULL DEFAULT '',
            codigo TEXT NOT NULL,
            descricao TEXT NOT NULL,
            ativo INTEGER NOT NULL DEFAULT 1,
            fonte TEXT NOT NULL DEFAULT 'BNCC/MEC 2018',
            UNIQUE(codigo, componente, ano_serie, unidade_tematica, objeto_conhecimento)
        );
        CREATE INDEX IF NOT EXISTS idx_bncc_contexto
            ON bncc_habilidades(etapa_ensino, componente, ano_serie, ativo);
        CREATE INDEX IF NOT EXISTS idx_bncc_unidade
            ON bncc_habilidades(unidade_tematica);
        CREATE INDEX IF NOT EXISTS idx_bncc_objeto
            ON bncc_habilidades(objeto_conhecimento);
        CREATE INDEX IF NOT EXISTS idx_bncc_codigo
            ON bncc_habilidades(codigo);
        ''')
        banco.commit()
    finally:
        banco.close()


def _ano_compativel(ano_registro: str, ano_selecionado: str, etapa: str) -> bool:
    a = _normalizar(ano_registro)
    s = _normalizar(ano_selecionado)
    if not s or not a:
        return True
    if _normalizar(etapa) == 'ensino medio':
        return True
    numero = ''.join(ch for ch in s if ch.isdigit())
    if not numero:
        return a == s
    return numero in a or a == s


def consultar_bncc(db_path: str, *, etapa: str, componente: str, ano_serie: str = '',
                   unidade: str = '', objeto: str = '') -> dict[str, list[Any]]:
    garantir_tabela_bncc(db_path)
    componente_consulta = componente_catalogo(componente, etapa)
    banco = sqlite3.connect(db_path)
    banco.row_factory = sqlite3.Row
    try:
        linhas = banco.execute('''
            SELECT etapa_ensino, ano_serie, area_conhecimento, componente,
                   unidade_tematica, objeto_conhecimento, codigo, descricao
            FROM bncc_habilidades
            WHERE ativo = 1
              AND LOWER(TRIM(etapa_ensino)) = LOWER(TRIM(?))
              AND (
                    LOWER(TRIM(componente)) = LOWER(TRIM(?))
                    OR LOWER(TRIM(area_conhecimento)) = LOWER(TRIM(?))
                  )
            ORDER BY unidade_tematica COLLATE NOCASE,
                     objeto_conhecimento COLLATE NOCASE,
                     codigo COLLATE NOCASE
        ''', (etapa, componente_consulta, componente_consulta)).fetchall()
    finally:
        banco.close()

    linhas = [r for r in linhas if _ano_compativel(r['ano_serie'], ano_serie, etapa)]
    if unidade:
        linhas = [r for r in linhas if _normalizar(r['unidade_tematica']) == _normalizar(unidade)]
    if objeto:
        linhas = [r for r in linhas if _normalizar(r['objeto_conhecimento']) == _normalizar(objeto)]

    unidades = sorted({(r['unidade_tematica'] or '').strip() for r in linhas if (r['unidade_tematica'] or '').strip()}, key=str.casefold)
    objetos = sorted({(r['objeto_conhecimento'] or '').strip() for r in linhas if (r['objeto_conhecimento'] or '').strip()}, key=str.casefold)
    habilidades = [
        {
            'codigo': r['codigo'],
            'descricao': r['descricao'],
            'valor': f"{r['codigo']} — {r['descricao']}",
            'unidade_tematica': r['unidade_tematica'] or '',
            'objeto_conhecimento': r['objeto_conhecimento'] or '',
        }
        for r in linhas
    ]
    return {'unidades': unidades, 'objetos': objetos, 'habilidades': habilidades}