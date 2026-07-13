import os
import shutil
import sqlite3
from datetime import datetime


ARQUIVO_BANCO = "plataforma.db"


def coluna_existe(cursor, tabela, coluna):
    cursor.execute(f"PRAGMA table_info({tabela})")
    colunas = cursor.fetchall()

    return any(item[1] == coluna for item in colunas)


def tabela_existe(cursor, tabela):
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name = ?
    """, (tabela,))

    return cursor.fetchone() is not None


def adicionar_coluna_se_necessario(cursor, tabela, coluna):
    if not tabela_existe(cursor, tabela):
        print(f"⚠️ Tabela '{tabela}' não encontrada. Ignorada.")
        return

    if coluna_existe(cursor, tabela, coluna):
        print(f"✅ A coluna '{tabela}.{coluna}' já existe.")
        return

    cursor.execute(
        f"ALTER TABLE {tabela} "
        f"ADD COLUMN {coluna} INTEGER"
    )

    print(f"✅ Coluna '{tabela}.{coluna}' criada.")


def criar_backup():
    if not os.path.exists(ARQUIVO_BANCO):
        raise FileNotFoundError(
            f"O banco '{ARQUIVO_BANCO}' não foi encontrado."
        )

    data_hora = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_backup = f"plataforma_backup_antes_anos_{data_hora}.db"

    shutil.copy2(ARQUIVO_BANCO, nome_backup)

    print(f"✅ Backup criado: {nome_backup}")


def executar_migracao():
    criar_backup()

    banco = sqlite3.connect(ARQUIVO_BANCO)
    banco.row_factory = sqlite3.Row
    cursor = banco.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys = ON")

        # =====================================================
        # TABELA DE ANOS LETIVOS
        # =====================================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anos_letivos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                escola_id INTEGER NOT NULL,
                ano INTEGER NOT NULL,
                data_inicio TEXT,
                data_fim TEXT,
                ativo INTEGER NOT NULL DEFAULT 0,
                encerrado INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (escola_id)
                    REFERENCES escolas(id)
                    ON DELETE CASCADE,

                UNIQUE (escola_id, ano)
            )
        """)

        print("✅ Tabela 'anos_letivos' criada ou confirmada.")

        # =====================================================
        # ÍNDICES
        # =====================================================

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_anos_letivos_escola
            ON anos_letivos(escola_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_anos_letivos_ativo
            ON anos_letivos(escola_id, ativo)
        """)

        # =====================================================
        # ADICIONAR ANO LETIVO NAS TABELAS PRINCIPAIS
        # =====================================================

        tabelas_com_ano_letivo = [
            "turmas",
            "alunos",
            "provas"
        ]

        for tabela in tabelas_com_ano_letivo:
            adicionar_coluna_se_necessario(
                cursor,
                tabela,
                "ano_letivo_id"
            )

        # =====================================================
        # CRIAR ANOS LETIVOS COM BASE NAS ESCOLAS EXISTENTES
        # =====================================================

        if coluna_existe(cursor, "escolas", "ano_letivo"):

            cursor.execute("""
                SELECT id, ano_letivo
                FROM escolas
            """)

            escolas = cursor.fetchall()

            for escola in escolas:
                escola_id = escola["id"]
                ano_atual = escola["ano_letivo"]

                try:
                    ano_atual = int(str(ano_atual).strip())
                except (TypeError, ValueError):
                    ano_atual = datetime.now().year

                cursor.execute("""
                    INSERT OR IGNORE INTO anos_letivos (
                        escola_id,
                        ano,
                        ativo,
                        encerrado
                    )
                    VALUES (?, ?, 1, 0)
                """, (
                    escola_id,
                    ano_atual
                ))

                # Garante somente um ano ativo por escola
                cursor.execute("""
                    UPDATE anos_letivos
                    SET ativo = CASE
                        WHEN ano = ? THEN 1
                        ELSE 0
                    END
                    WHERE escola_id = ?
                """, (
                    ano_atual,
                    escola_id
                ))

        else:
            print(
                "⚠️ A tabela escolas não possui a coluna "
                "'ano_letivo'. Será usado o ano atual."
            )

            cursor.execute("""
                SELECT id
                FROM escolas
            """)

            escolas = cursor.fetchall()
            ano_atual = datetime.now().year

            for escola in escolas:
                cursor.execute("""
                    INSERT OR IGNORE INTO anos_letivos (
                        escola_id,
                        ano,
                        ativo,
                        encerrado
                    )
                    VALUES (?, ?, 1, 0)
                """, (
                    escola["id"],
                    ano_atual
                ))

        # =====================================================
        # VINCULAR TURMAS EXISTENTES AO ANO ATIVO
        # =====================================================

        if tabela_existe(cursor, "turmas"):

            cursor.execute("""
                UPDATE turmas
                SET ano_letivo_id = (
                    SELECT al.id
                    FROM anos_letivos al
                    WHERE al.escola_id = turmas.escola_id
                    AND al.ativo = 1
                    LIMIT 1
                )
                WHERE ano_letivo_id IS NULL
            """)

        # =====================================================
        # VINCULAR ALUNOS EXISTENTES
        # =====================================================

        if tabela_existe(cursor, "alunos"):

            if coluna_existe(cursor, "alunos", "escola_id"):

                cursor.execute("""
                    UPDATE alunos
                    SET ano_letivo_id = (
                        SELECT al.id
                        FROM anos_letivos al
                        WHERE al.escola_id = alunos.escola_id
                        AND al.ativo = 1
                        LIMIT 1
                    )
                    WHERE ano_letivo_id IS NULL
                """)

            elif coluna_existe(cursor, "alunos", "turma_id"):

                cursor.execute("""
                    UPDATE alunos
                    SET ano_letivo_id = (
                        SELECT t.ano_letivo_id
                        FROM turmas t
                        WHERE t.id = alunos.turma_id
                        LIMIT 1
                    )
                    WHERE ano_letivo_id IS NULL
                """)

        # =====================================================
        # VINCULAR PROVAS EXISTENTES
        # =====================================================

        if tabela_existe(cursor, "provas"):

            if coluna_existe(cursor, "provas", "escola_id"):

                cursor.execute("""
                    UPDATE provas
                    SET ano_letivo_id = (
                        SELECT al.id
                        FROM anos_letivos al
                        WHERE al.escola_id = provas.escola_id
                        AND al.ativo = 1
                        LIMIT 1
                    )
                    WHERE ano_letivo_id IS NULL
                """)

            elif coluna_existe(cursor, "provas", "turma_id"):

                cursor.execute("""
                    UPDATE provas
                    SET ano_letivo_id = (
                        SELECT t.ano_letivo_id
                        FROM turmas t
                        WHERE t.id = provas.turma_id
                        LIMIT 1
                    )
                    WHERE ano_letivo_id IS NULL
                """)

        banco.commit()

        # =====================================================
        # RESULTADO
        # =====================================================

        cursor.execute("""
            SELECT
                al.id,
                al.escola_id,
                e.nome_instituicao,
                al.ano,
                al.ativo,
                al.encerrado
            FROM anos_letivos al
            LEFT JOIN escolas e
                ON e.id = al.escola_id
            ORDER BY e.nome_instituicao, al.ano DESC
        """)

        anos = cursor.fetchall()

        print("\n========================================")
        print("ANOS LETIVOS CADASTRADOS")
        print("========================================")

        for item in anos:
            print(
                f"ID: {item['id']} | "
                f"Instituição: {item['nome_instituicao']} | "
                f"Ano: {item['ano']} | "
                f"Ativo: {item['ativo']} | "
                f"Encerrado: {item['encerrado']}"
            )

        print("\n✅ Migração concluída com sucesso.")

    except Exception as erro:
        banco.rollback()

        print("\n❌ A migração não foi concluída.")
        print(f"Erro: {erro}")

        raise

    finally:
        banco.close()


if __name__ == "__main__":
    executar_migracao()