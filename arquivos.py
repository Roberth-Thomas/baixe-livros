"""Nomes de arquivo seguros, validação do que foi baixado e publicação na pasta final."""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Callable

from erros import FalhaDownload, TipoNaoPermitido

# Tipos aceitos como "livro"; só os de EXT_ABRIR são abertos pelo botão "Abrir".
EXT_LIVROS = (".pdf", ".epub", ".mobi", ".azw", ".azw3", ".doc", ".docx", ".txt", ".zip", ".rar")
EXT_ABRIR = (".pdf", ".epub", ".mobi", ".azw", ".azw3", ".doc", ".docx", ".txt")
RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
              *(f"LPT{i}" for i in range(1, 10))}
ASSINATURA_ZIP = (b"PK\x03\x04", b"PK\x05\x06")


def nome_seguro(titulo: str, pasta: str = "", ext: str = "") -> str:
    """Nome de arquivo válido no Windows a partir do título (sem extensão)."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", titulo).replace("…", "")
    t = re.sub(r"\s+", " ", t)
    limite = max(20, min(120, 250 - len(pasta) - len(ext) - 12))
    t = t[:limite].strip(" .") or "livro"
    if t.split(".")[0].strip().upper() in RESERVADOS:
        t = "_" + t
    return t


def sha256_arquivo(caminho: str) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def validar_arquivo(caminho: str) -> None:
    """Confere extensão permitida, tamanho e a assinatura do formato (PDF, EPUB/DOCX/ZIP)."""
    ext = os.path.splitext(caminho)[1].lower()
    if ext not in EXT_LIVROS:
        raise TipoNaoPermitido(f"tipo de arquivo não permitido ({ext or 'sem extensão'})")
    if os.path.getsize(caminho) == 0:
        raise FalhaDownload("o arquivo baixado está vazio")
    with open(caminho, "rb") as f:
        inicio = f.read(1024)
    if ext == ".pdf" and b"%PDF-" not in inicio:
        raise FalhaDownload("o arquivo baixado não é um PDF válido")
    if ext in (".epub", ".docx", ".zip") and not inicio.startswith(ASSINATURA_ZIP):
        raise FalhaDownload(f"o arquivo baixado não é um {ext[1:].upper()} válido")
    if ext == ".rar" and not inicio.startswith(b"Rar!"):
        raise FalhaDownload("o arquivo baixado não é um RAR válido")


def publicar(origem: str, pasta: str, titulo: str, log: Callable[[str], None]) -> str:
    """Move o arquivo baixado para a pasta final, com o título do livro como nome. Nunca sobrescreve."""
    ext = os.path.splitext(origem)[1].lower()
    base = nome_seguro(titulo, pasta, ext)
    for n in range(1, 100):
        nome = base + ("" if n == 1 else f" ({n})") + ext
        destino = os.path.join(pasta, nome)
        if os.path.exists(destino):
            continue
        for _ in range(10):
            try:
                os.rename(origem, destino)  # falha (em vez de sobrescrever) se já existir
                return nome
            except FileExistsError:
                break
            except PermissionError:  # antivírus ou indexador ainda segurando o arquivo
                time.sleep(0.3)
            except OSError as e:
                log(f"Aviso: não foi possível salvar como “{nome}” ({e}); mantendo o nome original.")
                original = nome_seguro(os.path.splitext(os.path.basename(origem))[0], pasta, ext) + ext
                os.rename(origem, os.path.join(pasta, original))
                return original
    raise OSError(f"não foi possível salvar “{base}{ext}” na pasta de destino")
