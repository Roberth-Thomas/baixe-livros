"""Biblioteca pessoal: registro (SQLite) de tudo que já foi baixado, para evitar duplicatas e permitir busca."""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import NamedTuple

from titulos import tokens


class Livro(NamedTuple):
    id: int
    titulo: str
    autor: str
    fonte: str
    fonte_id: str
    arquivo: str       # nome do arquivo dentro da pasta de downloads
    sha256: str
    tamanho: int
    licenca: str
    baixado_em: str    # UTC, ISO 8601: 2026-10-04T13:45:00Z


_COLUNAS = "id, titulo, autor, fonte, fonte_id, arquivo, sha256, tamanho, licenca, baixado_em"


class Biblioteca:
    def __init__(self, caminho: str = ":memory:"):
        self._con = sqlite3.connect(caminho, check_same_thread=False)
        self._trava = threading.Lock()
        with self._trava, self._con:
            self._con.execute("""CREATE TABLE IF NOT EXISTS livros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL, autor TEXT NOT NULL DEFAULT '',
                fonte TEXT NOT NULL, fonte_id TEXT NOT NULL,
                arquivo TEXT NOT NULL, sha256 TEXT NOT NULL, tamanho INTEGER NOT NULL DEFAULT 0,
                licenca TEXT NOT NULL DEFAULT '', baixado_em TEXT NOT NULL,
                busca TEXT NOT NULL DEFAULT '',
                UNIQUE (fonte, fonte_id))""")
            self._con.execute("CREATE INDEX IF NOT EXISTS idx_livros_sha ON livros (sha256)")

    def registrar(self, *, titulo: str, autor: str, fonte: str, fonte_id: str, arquivo: str, sha256: str,
                  tamanho: int, licenca: str = "") -> Livro:
        busca = " ".join(tokens(f"{titulo} {autor}"))
        agora = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._trava, self._con:
            self._con.execute(
                "INSERT INTO livros (titulo, autor, fonte, fonte_id, arquivo, sha256, tamanho, licenca, baixado_em, busca) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT (fonte, fonte_id) DO UPDATE SET titulo=excluded.titulo, autor=excluded.autor, "
                "arquivo=excluded.arquivo, sha256=excluded.sha256, tamanho=excluded.tamanho, "
                "licenca=excluded.licenca, baixado_em=excluded.baixado_em, busca=excluded.busca",
                (titulo, autor, fonte, fonte_id, arquivo, sha256, tamanho, licenca, agora, busca))
            linha = self._con.execute(f"SELECT {_COLUNAS} FROM livros WHERE fonte=? AND fonte_id=?",
                                      (fonte, fonte_id)).fetchone()
        return Livro(*linha)

    def _um(self, onde: str, args: tuple) -> Livro | None:
        with self._trava:
            linha = self._con.execute(f"SELECT {_COLUNAS} FROM livros WHERE {onde} ORDER BY id LIMIT 1", args).fetchone()
        return Livro(*linha) if linha else None

    def por_fonte(self, fonte: str, fonte_id: str) -> Livro | None:
        return self._um("fonte=? AND fonte_id=?", (fonte, fonte_id))

    def por_hash(self, sha256: str) -> Livro | None:
        return self._um("sha256=?", (sha256,))

    def listar(self, filtro: str = "") -> list[Livro]:
        """Mais recentes primeiro. O filtro ignora acentos/maiúsculas e exige todas as palavras (título ou autor)."""
        palavras = tokens(filtro)   # só letras/números: nem curingas do LIKE nem SQL chegam à consulta
        onde = " AND ".join("busca LIKE ?" for _ in palavras) or "1=1"
        args = tuple(f"%{p}%" for p in palavras)
        with self._trava:
            linhas = self._con.execute(
                f"SELECT {_COLUNAS} FROM livros WHERE {onde} ORDER BY baixado_em DESC, id DESC", args).fetchall()
        return [Livro(*r) for r in linhas]

    def remover(self, livro_id: int) -> None:
        """Remove só o registro (o arquivo no disco não é tocado)."""
        with self._trava, self._con:
            self._con.execute("DELETE FROM livros WHERE id=?", (livro_id,))

    def fechar(self) -> None:
        with self._trava:
            self._con.close()
