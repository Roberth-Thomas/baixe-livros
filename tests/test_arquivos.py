"""Testes de validação e publicação de arquivos."""
import os

import pytest

import arquivos as a
from erros import FalhaDownload, TipoNaoPermitido
from helpers import EPUB, PDF


def grava(caminho, dados):
    caminho.write_bytes(dados)
    return str(caminho)


class TestValidarArquivo:
    def test_aceita_formatos_validos(self, tmp_path):
        a.validar_arquivo(grava(tmp_path / "a.pdf", PDF))
        a.validar_arquivo(grava(tmp_path / "a.epub", EPUB))
        a.validar_arquivo(grava(tmp_path / "a.txt", b"texto qualquer"))
        a.validar_arquivo(grava(tmp_path / "a.rar", b"Rar!\x1a\x07\x00..."))

    @pytest.mark.parametrize("nome,dados,exc,trecho", [
        ("a.pdf", b"", FalhaDownload, "vazio"),
        ("a.pdf", b"<html>erro 403</html>", FalhaDownload, "PDF válido"),
        ("a.epub", b"%PDF-1.4 disfarcado", FalhaDownload, "EPUB válido"),
        ("a.zip", b"nao e zip", FalhaDownload, "ZIP válido"),
        ("a.rar", b"PK\x03\x04", FalhaDownload, "RAR válido"),
        ("a.exe", b"MZ\x90\x00", TipoNaoPermitido, ".exe"),
        ("a.bat", b"@echo off", TipoNaoPermitido, ".bat"),
        ("a.lnk", b"L\x00\x00\x00", TipoNaoPermitido, ".lnk"),
        ("semextensao", b"x", TipoNaoPermitido, "sem extensão"),
        ("a.pdf.exe", b"%PDF-1.4", TipoNaoPermitido, ".exe"),
    ])
    def test_recusa_conteudo_invalido(self, tmp_path, nome, dados, exc, trecho):
        with pytest.raises(exc, match=trecho):
            a.validar_arquivo(grava(tmp_path / nome, dados))

    def test_extensao_em_maiusculas(self, tmp_path):
        a.validar_arquivo(grava(tmp_path / "A.PDF", PDF))


class TestSha256:
    def test_valor_conhecido_e_arquivo_grande(self, tmp_path):
        assert a.sha256_arquivo(grava(tmp_path / "x", b"abc")) == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        assert len(a.sha256_arquivo(grava(tmp_path / "g", b"z" * (3 * 1024 * 1024 + 7)))) == 64


class TestPublicar:
    def test_renomeia_para_o_titulo(self, tmp_path):
        origem = tmp_path / "x.part.pdf"
        origem.write_bytes(PDF)
        nome = a.publicar(str(origem), str(tmp_path), "Dom Casmurro – Machado de Assis", lambda m: None)
        assert nome == "Dom Casmurro – Machado de Assis.pdf" and (tmp_path / nome).exists() and not origem.exists()

    def test_nao_sobrescreve(self, tmp_path):
        (tmp_path / "Livro.pdf").write_bytes(b"antigo")
        origem = tmp_path / "novo.pdf"
        origem.write_bytes(PDF)
        assert a.publicar(str(origem), str(tmp_path), "Livro", lambda m: None) == "Livro (2).pdf"
        assert (tmp_path / "Livro.pdf").read_bytes() == b"antigo"

    def test_titulo_reservado_do_windows(self, tmp_path):
        origem = tmp_path / "dl.pdf"
        origem.write_bytes(PDF)
        assert a.publicar(str(origem), str(tmp_path), "NUL", lambda m: None) == "_NUL.pdf"

    def test_path_traversal_no_titulo(self, tmp_path):
        pasta = tmp_path / "livros"
        pasta.mkdir()
        origem = tmp_path / "dl.pdf"
        origem.write_bytes(PDF)
        nome = a.publicar(str(origem), str(pasta), "../../fora", lambda m: None)
        assert os.path.dirname(os.path.join(str(pasta), nome)) == str(pasta) and (pasta / nome).exists()

    def test_oserror_mantem_o_nome_original_e_avisa(self, tmp_path, monkeypatch):
        origem = tmp_path / "original.pdf"
        origem.write_bytes(PDF)
        real, chamadas, logs = os.rename, [], []

        def rename(x, y):
            chamadas.append(y)
            if len(chamadas) == 1:
                raise OSError("nome inválido")
            return real(x, y)
        monkeypatch.setattr(a.os, "rename", rename)
        assert a.publicar(str(origem), str(tmp_path), "Livro", logs.append) == "original.pdf"
        assert any("Aviso" in m for m in logs)
