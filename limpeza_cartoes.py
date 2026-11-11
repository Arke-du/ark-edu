"""Limpeza conservadora de imagens de cartões; nunca apaga respostas no SQLite."""
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger('ark_edus.limpeza_cartoes')
RETENCAO_DIAS = 15


def executar_limpeza(db_path, upload_folder, limite=50, agora=None):
    """Remove somente imagens de cartões concluídos com respostas persistidas.

    Não toca em provas, anexos gerais, PDFs de upload, respostas ou backups.
    """
    agora = agora or datetime.now(timezone.utc)
    corte = (agora - timedelta(days=RETENCAO_DIAS)).strftime('%Y-%m-%d %H:%M:%S')
    raiz = Path(upload_folder).resolve()
    pasta_cartoes = (raiz / 'aplicacoes').resolve()
    total = 0
    bytes_liberados = 0
    ignorados = 0
    if not Path(db_path).is_file() or not pasta_cartoes.is_dir():
        return {'excluidos': 0, 'bytes': 0, 'ignorados': 0}
    banco = sqlite3.connect(db_path, timeout=8)
    banco.row_factory = sqlite3.Row
    try:
        # Não executa em banco legado sem tabelas de correção.
        tabelas = {r[0] for r in banco.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        necessarias = {'aplicacao_importacoes', 'aplicacao_alunos', 'aplicacao_respostas_objetivas'}
        if not necessarias.issubset(tabelas):
            return {'excluidos': 0, 'bytes': 0, 'ignorados': 0}
        candidatos = banco.execute('''
            SELECT ai.id, ai.aplicacao_id, ai.aluno_id, ai.caminho_arquivo,
                   aa.total_objetivas
            FROM aplicacao_importacoes ai
            JOIN aplicacao_alunos aa
              ON aa.aplicacao_id=ai.aplicacao_id AND aa.aluno_id=ai.aluno_id
            WHERE ai.importado_em < ?
              AND ai.caminho_arquivo IS NOT NULL AND ai.caminho_arquivo != ''
              AND ai.tipo_folha='OBJETIVAS'
              AND ai.status IN ('Processado', 'Corrigido')
              AND ai.revisado=1 AND aa.objetiva_corrigida=1
              AND COALESCE(aa.discursiva_pendente, 0)=0
              AND aa.total_objetivas > 0
              AND (SELECT COUNT(*) FROM aplicacao_respostas_objetivas r
                   WHERE r.aplicacao_id=ai.aplicacao_id AND r.aluno_id=ai.aluno_id)
                  >= aa.total_objetivas
            ORDER BY ai.importado_em ASC LIMIT ?
        ''', (corte, max(1, min(int(limite), 200)))).fetchall()
        for item in candidatos:
            caminho = Path(item['caminho_arquivo']).resolve()
            # Só imagens produzidas na importação, nunca uploads genéricos.
            if pasta_cartoes not in caminho.parents or caminho.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
                ignorados += 1
                continue
            # Não apagar arquivo compartilhado com revisão, falha ou outro cartão.
            referencias = banco.execute('''
                SELECT COUNT(*) FROM aplicacao_importacoes
                WHERE caminho_arquivo=? AND id != ?
            ''', (item['caminho_arquivo'], item['id'])).fetchone()[0]
            if referencias:
                ignorados += 1
                continue
            try:
                if caminho.is_symlink():
                    ignorados += 1
                    continue
                tamanho = caminho.stat().st_size if caminho.is_file() else 0
                if caminho.is_file():
                    caminho.unlink()
                # Preserva linha de auditoria e respostas; apenas remove referência à imagem.
                banco.execute('''UPDATE aplicacao_importacoes SET caminho_arquivo=''
                                 WHERE id=? AND caminho_arquivo=?''',
                              (item['id'], item['caminho_arquivo']))
                banco.commit()
                total += 1
                bytes_liberados += tamanho
            except (OSError, sqlite3.Error):
                banco.rollback()
                LOG.exception('Erro ao remover imagem de cartão id=%s', item['id'])
        LOG.info('LIMPEZA_CARTOES: excluidos=%d bytes=%d ignorados=%d', total, bytes_liberados, ignorados)
        return {'excluidos': total, 'bytes': bytes_liberados, 'ignorados': ignorados}
    finally:
        banco.close()


def iniciar_rotina_automatica(db_path, upload_folder):
    """Agenda leve, sem cron externo; bloqueio entre workers e execução diária."""
    import threading
    import fcntl
    def trabalho():
        controle = Path(upload_folder).resolve().parent / '.limpeza_cartoes_ultima_execucao'
        trava = Path('/tmp/ark_edus_limpeza_cartoes.lock')
        while True:
            try:
                with open(trava, 'a+') as arquivo_trava:
                    if fcntl.flock(arquivo_trava, fcntl.LOCK_EX | fcntl.LOCK_NB) is None:
                        try:
                            ultima = float(controle.read_text()) if controle.is_file() else 0
                            if time.time() - ultima >= 86400:
                                resultado = executar_limpeza(db_path, upload_folder)
                                controle.write_text(str(time.time()))
                                LOG.info('LIMPEZA_CARTOES: rotina diária %s', resultado)
                        finally:
                            fcntl.flock(arquivo_trava, fcntl.LOCK_UN)
            except BlockingIOError:
                pass
            except Exception:
                LOG.exception('LIMPEZA_CARTOES: falha na rotina diária')
            time.sleep(3600)
    threading.Thread(target=trabalho, daemon=True, name='limpeza-cartoes').start()
