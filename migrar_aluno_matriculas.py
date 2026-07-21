import os
import shutil
import sqlite3
from datetime import datetime


BANCO = "plataforma.db"


def coluna_existe(cursor, tabela, coluna):
    cursor.execute(f"PRAGMA table_info({tabela})")
    colunas = [linha[1] for linha in cursor.fetchall()]
    return coluna in colunas


def tabela_existe(cursor, tabela):
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
    """, (tabela,))

    return cursor.fetchone() is not None


def fazer_backup():
    data = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_backup = f"plataforma_backup_matriculas_{data}.db"

    shutil.copy2(BANCO, nome_backup)

    print(f"Backup criado: {nome_backup}")


def migrar():
    if not os.path.exists(BANCO):
        print(f"Erro: banco {BANCO} não encontrado.")
        return

    fazer_backup()

    banco = sqlite3.connect(BANCO)
    cursor = banco.cursor()

    try:

        cursor.execute("PRAGMA foreign_keys = ON")

        # =====================================================
        # CRIAR TABELA DE MATRÍCULAS
        # =====================================================

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

                FOREIGN KEY (aluno_id)
                    REFERENCES alunos(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (escola_id)
                    REFERENCES escolas(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (ano_letivo_id)
                    REFERENCES anos_letivos(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (turma_id)
                    REFERENCES turmas(id)
                    ON DELETE CASCADE,

                UNIQUE (
                    aluno_id,
                    ano_letivo_id
                )
            )
        """)

        # =====================================================
        # MIGRAR MATRÍCULAS EXISTENTES
        # =====================================================

        cursor.execute("""
            SELECT
                id,
                escola_id,
                ano_letivo_id,
                turma_id
            FROM alunos
            WHERE turma_id IS NOT NULL
              AND ano_letivo_id IS NOT NULL
        """)

        alunos = cursor.fetchall()

        total_inseridos = 0
        total_existentes = 0

        for aluno in alunos:

            aluno_id = aluno[0]
            escola_id = aluno[1]
            ano_letivo_id = aluno[2]
            turma_id = aluno[3]

            cursor.execute("""
                SELECT id
                FROM aluno_matriculas
                WHERE aluno_id = ?
                  AND ano_letivo_id = ?
                LIMIT 1
            """, (
                aluno_id,
                ano_letivo_id
            ))

            matricula_existente = cursor.fetchone()

            if matricula_existente:
                total_existentes += 1
                continue

            cursor.execute("""
                INSERT INTO aluno_matriculas (
                    aluno_id,
                    escola_id,
                    ano_letivo_id,
                    turma_id,
                    situacao
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                aluno_id,
                escola_id,
                ano_letivo_id,
                turma_id,
                "Cursando"
            ))

            total_inseridos += 1

        # =====================================================
        # CRIAR ÍNDICES
        # =====================================================

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_aluno_matriculas_aluno
            ON aluno_matriculas(aluno_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_aluno_matriculas_ano
            ON aluno_matriculas(ano_letivo_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_aluno_matriculas_turma
            ON aluno_matriculas(turma_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_aluno_matriculas_escola
            ON aluno_matriculas(escola_id)
        """)

        banco.commit()

        print("")
        print("Migração concluída com sucesso.")
        print(f"Novas matrículas criadas: {total_inseridos}")
        print(f"Matrículas que já existiam: {total_existentes}")

    except Exception as erro:

        banco.rollback()

        print("")
        print("Erro durante a migração:")
        print(erro)

        raise

    finally:
        banco.close()


if __name__ == "__main__":
    migrar()