"""Integração com as APIs reais (Internet Archive e Project Gutenberg). Rode com: pytest -m integration

Detectam mudanças nas APIs. Um teste que falha aqui por falta de internet ou por uma fonte fora do ar
não indica, por si só, um defeito no programa.
"""
import os
import urllib.parse

import pytest

import arquivos
import nucleo
from biblioteca import Biblioteca
from fontes import FONTES, Gutenberg, InternetArchive
from rede import Http

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def http():
    return Http(timeout=60)


def test_internet_archive_acha_o_dom_casmurro_e_lista_o_arquivo(http):
    ia = InternetArchive()
    obras = ia.buscar("dom casmurro", http)
    assert any("casmurro" in o.titulo.lower() for o in obras)
    obra = next(o for o in obras if o.id == "domcasmurro_1899")
    arqs = ia.arquivos(obra, http)
    assert arqs and arqs[0].ext == ".pdf" and urllib.parse.urlsplit(arqs[0].url).hostname == "archive.org"
    assert http.permitida(arqs[0].url)


def test_internet_archive_erro_de_digitacao_no_inicio_da_palavra(http):
    titulos = [o.titulo.lower() for o in InternetArchive().buscar("murro", http)]
    assert any("casmurro" in t for t in titulos)


def test_gutenberg_acha_o_dom_casmurro_e_baixa_o_epub(http, tmp_path):
    g = Gutenberg()
    obras = g.buscar("dom casmurro", http)
    obra = next(o for o in obras if "casmurro" in o.titulo.lower())
    arq = g.arquivos(obra, http)[0]
    assert arq.ext == ".epub" and http.permitida(arq.url)
    destino = str(tmp_path / "livro.epub")
    n = http.baixar(arq.url, destino)
    assert n > 10_000
    arquivos.validar_arquivo(destino)


def test_fluxo_completo_com_as_duas_fontes_e_erro_de_digitacao(http, tmp_path):
    ctx = nucleo.Contexto(pasta=str(tmp_path / "livros"), http=http, fontes=FONTES, biblioteca=Biblioteca())
    r = nucleo.processar("cazmurro", ctx)
    assert "casmurro" in r.nome.lower() and not r.ja_existia
    caminho = os.path.join(ctx.pasta, r.arquivo)
    arquivos.validar_arquivo(caminho)
    assert ctx.biblioteca.por_fonte(r.fonte, ctx.biblioteca.listar()[0].fonte_id).arquivo == r.arquivo


def test_livro_inexistente(http, tmp_path):
    from erros import LivroNaoEncontrado
    ctx = nucleo.Contexto(pasta=str(tmp_path / "livros"), http=http, fontes=FONTES, biblioteca=Biblioteca())
    with pytest.raises(LivroNaoEncontrado):
        nucleo.processar("zzqxqw kkjjhh", ctx)
