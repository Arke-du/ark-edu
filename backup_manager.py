"""Backups automáticos da ARK EDUS.

O módulo usa a API de backup do SQLite, permitindo gerar uma cópia consistente
mesmo com a plataforma em uso. Os arquivos são compactados, validados e
mantidos conforme a política de retenção configurada.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet
except Exception:  # pragma: no cover
    Fernet = None


class BackupError(RuntimeError):
    pass


class BackupManager:
    def __init__(self, app, db_path: str, upload_folder: str | None = None):
        self.app = app
        self.db_path = Path(db_path).resolve()
        self.upload_folder = Path(upload_folder).resolve() if upload_folder else None
        self.retention_days = max(1, int(os.environ.get("BACKUP_RETENTION_DAYS", "3")))
        self.interval_hours = max(1, int(os.environ.get("BACKUP_INTERVAL_HOURS", "24")))
        self.include_uploads = os.environ.get("BACKUP_INCLUDE_UPLOADS", "false").lower() == "true"
        self.enabled = os.environ.get("BACKUP_AUTOMATICO", "true").lower() == "true"
        self.encryption_key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()
        self.backup_dir = self._preparar_diretorio_backup()
        self._thread_started = False
        # Libera espaço antes de qualquer novo backup. Isso é essencial em discos
        # pequenos do Render: versões antigas incluíam todos os uploads em cada ZIP.
        self._limpar_backups_por_arquivo()

    def _preparar_diretorio_backup(self) -> Path:
        """Seleciona um diretório gravável sem impedir a inicialização da aplicação.

        Prioridade:
        1. BACKUP_DIR, quando configurado;
        2. RENDER_DISK_PATH/backups, quando houver disco persistente;
        3. pasta backups ao lado do banco;
        4. pasta backups na raiz da aplicação;
        5. diretório temporário do sistema.
        """
        candidatos: list[Path] = []

        backup_configurado = os.environ.get("BACKUP_DIR", "").strip()
        if backup_configurado:
            candidatos.append(Path(backup_configurado).expanduser())

        render_disk = os.environ.get("RENDER_DISK_PATH", "").strip()
        if render_disk:
            candidatos.append(Path(render_disk).expanduser() / "backups")

        candidatos.extend(
            [
                self.db_path.parent / "backups",
                Path(self.app.root_path) / "backups",
                Path(tempfile.gettempdir()) / "ark_edus_backups",
            ]
        )

        tentados: set[str] = set()
        erros: list[str] = []

        for candidato in candidatos:
            try:
                diretorio = candidato.resolve()
                chave = str(diretorio)
                if chave in tentados:
                    continue
                tentados.add(chave)

                diretorio.mkdir(parents=True, exist_ok=True)

                # Confirma que o processo realmente possui permissão de escrita.
                teste = diretorio / ".ark_backup_write_test"
                teste.write_text("ok", encoding="utf-8")
                teste.unlink(missing_ok=True)

                if backup_configurado and diretorio != Path(backup_configurado).expanduser().resolve():
                    self.app.logger.warning(
                        "BACKUP_DIR não pôde ser usado. Backups serão gravados em %s.",
                        diretorio,
                    )
                return diretorio
            except (OSError, RuntimeError) as exc:
                erros.append(f"{candidato}: {exc}")
                self.app.logger.warning(
                    "Diretório de backup indisponível (%s): %s",
                    candidato,
                    exc,
                )

        raise BackupError(
            "Não foi possível preparar um diretório gravável para os backups. "
            + " | ".join(erros)
        )

    def garantir_tabela(self) -> None:
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS backups_sistema (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome_arquivo TEXT,
                    caminho TEXT,
                    tamanho_bytes INTEGER,
                    status TEXT NOT NULL DEFAULT 'iniciado',
                    tipo TEXT NOT NULL DEFAULT 'automatico',
                    criptografado INTEGER NOT NULL DEFAULT 0,
                    inclui_uploads INTEGER NOT NULL DEFAULT 0,
                    criado_por INTEGER,
                    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    concluido_em TEXT,
                    mensagem TEXT
                )
                """
            )
            db.commit()

    def _registrar(self, **campos: Any) -> int:
        self.garantir_tabela()
        chaves = list(campos)
        valores = [campos[k] for k in chaves]
        with sqlite3.connect(self.db_path) as db:
            cur = db.execute(
                f"INSERT INTO backups_sistema ({','.join(chaves)}) VALUES ({','.join('?' for _ in chaves)})",
                valores,
            )
            db.commit()
            return int(cur.lastrowid)

    def _atualizar(self, registro_id: int, **campos: Any) -> None:
        if not campos:
            return
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE backups_sistema SET " + ",".join(f"{k}=?" for k in campos) + " WHERE id=?",
                [*campos.values(), registro_id],
            )
            db.commit()

    def ultimo_backup_concluido(self):
        self.garantir_tabela()
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            return db.execute(
                "SELECT * FROM backups_sistema WHERE status='concluido' ORDER BY criado_em DESC LIMIT 1"
            ).fetchone()

    def backup_necessario(self) -> bool:
        ultimo = self.ultimo_backup_concluido()
        if not ultimo:
            return True
        try:
            criado = datetime.fromisoformat(str(ultimo["criado_em"]).replace("Z", "+00:00"))
            if criado.tzinfo is None:
                criado = criado.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - criado >= timedelta(hours=self.interval_hours)

    def _lock_path(self) -> Path:
        return self.backup_dir / ".backup.lock"

    def _adquirir_lock(self) -> int | None:
        lock = self._lock_path()
        try:
            if lock.exists() and time.time() - lock.stat().st_mtime > 60 * 60 * 3:
                lock.unlink(missing_ok=True)
            return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None

    def _liberar_lock(self, fd: int | None) -> None:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        self._lock_path().unlink(missing_ok=True)

    def _limpar_backups_por_arquivo(self) -> None:
        """Remove backups antigos diretamente pelo filesystem, mesmo com SQLite cheio."""
        try:
            arquivos = sorted(
                [p for p in self.backup_dir.glob("ark_edus_backup_*") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            limite = time.time() - (self.retention_days * 86400)
            # Mantém no máximo 3 backups. O banco continua sendo a fonte principal;
            # uploads não são mais duplicados diariamente por padrão.
            for indice, arquivo in enumerate(arquivos):
                if indice >= 3 or arquivo.stat().st_mtime < limite:
                    arquivo.unlink(missing_ok=True)
        except OSError:
            self.app.logger.exception("Falha ao liberar backups antigos do disco")

    def criar_backup(self, tipo: str = "manual", criado_por: int | None = None) -> dict[str, Any]:
        fd = self._adquirir_lock()
        if fd is None:
            raise BackupError("Já existe um backup em andamento.")

        registro_id = self._registrar(tipo=tipo, criado_por=criado_por, status="iniciado")
        agora = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_base = f"ark_edus_backup_{agora}"
        temp_dir = Path(tempfile.mkdtemp(prefix="ark_backup_"))
        zip_path = self.backup_dir / f"{nome_base}.zip"
        final_path = zip_path

        try:
            if not self.db_path.exists():
                raise BackupError(f"Banco não encontrado: {self.db_path}")

            copia_db = temp_dir / "plataforma.db"
            origem = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            destino = sqlite3.connect(copia_db)
            try:
                origem.backup(destino)
            finally:
                destino.close()
                origem.close()

            # Validação básica de integridade da cópia.
            with sqlite3.connect(copia_db) as db_teste:
                resultado = db_teste.execute("PRAGMA integrity_check").fetchone()[0]
                if resultado != "ok":
                    raise BackupError(f"Falha na integridade do banco: {resultado}")

            manifesto = {
                "aplicacao": "ARK EDUS",
                "criado_em": datetime.now(timezone.utc).isoformat(),
                "banco": self.db_path.name,
                "inclui_uploads": bool(self.include_uploads and self.upload_folder and self.upload_folder.exists()),
                "retencao_dias": self.retention_days,
                "formato": 1,
            }
            (temp_dir / "manifesto.json").write_text(
                json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                zf.write(copia_db, "plataforma.db")
                zf.write(temp_dir / "manifesto.json", "manifesto.json")
                if self.include_uploads and self.upload_folder and self.upload_folder.exists():
                    for arquivo in self.upload_folder.rglob("*"):
                        if arquivo.is_file():
                            zf.write(arquivo, Path("uploads") / arquivo.relative_to(self.upload_folder))

            with zipfile.ZipFile(zip_path, "r") as zf:
                if zf.testzip() is not None or "plataforma.db" not in zf.namelist():
                    raise BackupError("O arquivo ZIP gerado não passou na validação.")

            criptografado = 0
            if self.encryption_key:
                if Fernet is None:
                    raise BackupError("A biblioteca cryptography não está disponível para criptografar o backup.")
                try:
                    fernet = Fernet(self.encryption_key.encode("utf-8"))
                except Exception as exc:
                    raise BackupError("BACKUP_ENCRYPTION_KEY inválida. Use uma chave Fernet.") from exc
                final_path = zip_path.with_suffix(".zip.fernet")
                final_path.write_bytes(fernet.encrypt(zip_path.read_bytes()))
                zip_path.unlink(missing_ok=True)
                criptografado = 1

            tamanho = final_path.stat().st_size
            self._atualizar(
                registro_id,
                nome_arquivo=final_path.name,
                caminho=str(final_path),
                tamanho_bytes=tamanho,
                status="concluido",
                criptografado=criptografado,
                inclui_uploads=int(manifesto["inclui_uploads"]),
                concluido_em=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                mensagem="Backup validado com sucesso.",
            )
            self.aplicar_retencao()
            return {
                "id": registro_id,
                "nome_arquivo": final_path.name,
                "caminho": str(final_path),
                "tamanho_bytes": tamanho,
                "criptografado": bool(criptografado),
            }
        except Exception as exc:
            zip_path.unlink(missing_ok=True)
            if final_path != zip_path:
                final_path.unlink(missing_ok=True)
            self._atualizar(
                registro_id,
                status="falhou",
                concluido_em=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                mensagem=str(exc)[:1000],
            )
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._liberar_lock(fd)

    def aplicar_retencao(self) -> None:
        limite = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        self.garantir_tabela()
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            registros = db.execute(
                "SELECT id,caminho,criado_em FROM backups_sistema WHERE status='concluido' ORDER BY criado_em DESC"
            ).fetchall()
            for item in registros:
                try:
                    criado = datetime.fromisoformat(str(item["criado_em"]).replace("Z", "+00:00"))
                    if criado.tzinfo is None:
                        criado = criado.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if criado < limite:
                    caminho = Path(item["caminho"] or "")
                    if caminho.parent == self.backup_dir:
                        caminho.unlink(missing_ok=True)
                    db.execute(
                        "UPDATE backups_sistema SET status='expirado', mensagem='Removido pela política de retenção.' WHERE id=?",
                        (item["id"],),
                    )
            db.commit()

    def listar(self, limite: int = 100):
        self.garantir_tabela()
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            return db.execute(
                "SELECT * FROM backups_sistema ORDER BY criado_em DESC LIMIT ?", (limite,)
            ).fetchall()

    def iniciar_agendador(self) -> None:
        if not self.enabled or self._thread_started:
            return
        self._thread_started = True

        def executar():
            # Pequena espera para não disputar o banco durante o boot.
            time.sleep(15)
            while True:
                try:
                    if self.backup_necessario():
                        self.criar_backup(tipo="automatico")
                except BackupError:
                    pass
                except Exception:
                    self.app.logger.exception("Falha no backup automático")
                time.sleep(60 * 60)  # verifica uma vez por hora

        threading.Thread(target=executar, name="ark-backup", daemon=True).start()