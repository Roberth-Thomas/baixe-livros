"""Testes da biblioteca pessoal (SQLite)."""
import threading

import pytest

from biblioteca import Biblioteca


def reg(b, titulo="Dom Casmurro", autor="Machado de Assis", fonte="archive", fid="1", sha="aa", arquivo=None, **kw):
    return b.registrar(titulo=titulo, autor=autor, fonte=fonte, fonte_id=fid, arquivo=arquivo or f"{titulo}.pdf",
                       sha256=sha, tamanho=kw.pop("tamanho", 100), **kw)


@pytest.fixture
def b():
    bib = Biblioteca()
    yield bib
    bib.fechar()


def test_registrar_e_consultar(b):
    livro = reg(b, licenca="Domínio público")
    assert livro.id == 1 and livro.titulo == "Dom Casmurro" and livro.licenca == "Domínio público"
    assert b.por_fonte("archive", "1") == livro and b.por_hash("aa") == livro
    assert b.por_fonte("archive", "2") is None and b.por_hash("zz") is None


def test_data_em_utc_iso_8601(b):
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", reg(b).baixado_em)


def test_registrar_de_novo_atualiza_sem_duplicar(b):
    reg(b, arquivo="velho.pdf", sha="a1")
    novo = reg(b, arquivo="novo.pdf", sha="a2")
    assert len(b.listar()) == 1 and novo.arquivo == "novo.pdf" and b.por_hash("a1") is None


def test_listar_filtro_ignora_acentos_e_caixa_e_exige_todas_as_palavras(b):
    reg(b, "Memórias Póstumas de Brás Cubas", "Machado de Assis", fid="1", sha="1")
    reg(b, "Iracema", "José de Alencar", fid="2", sha="2")
    reg(b, "Dom Casmurro", "Machado de Assis", fid="3", sha="3")
    assert {l.titulo for l in b.listar("MEMORIAS postumas")} == {"Memórias Póstumas de Brás Cubas"}
    assert {l.titulo for l in b.listar("machado")} == {"Memórias Póstumas de Brás Cubas", "Dom Casmurro"}
    assert [l.titulo for l in b.listar("machado iracema")] == []
    assert len(b.listar()) == 3 and len(b.listar("   ")) == 3


def test_listar_do_mais_recente_para_o_mais_antigo(b):
    for i in range(3):
        reg(b, f"Livro {i}", fid=str(i), sha=str(i))
    assert [l.titulo for l in b.listar()] == ["Livro 2", "Livro 1", "Livro 0"]


def test_filtro_nao_e_vetor_de_injecao_sql(b):
    reg(b)
    assert b.listar("'; DROP TABLE livros; --") == []
    assert b.listar("100% _ \\") == []
    assert len(b.listar()) == 1


def test_titulo_com_aspas_e_caracteres_estranhos(b):
    livro = reg(b, "O'Brien \"Quote\"; DROP--", "Zé %_", fid="9", sha="9")
    assert b.por_fonte("archive", "9") == livro


def test_remover_so_apaga_o_registro(b):
    livro = reg(b)
    b.remover(livro.id)
    assert b.listar() == [] and b.por_hash("aa") is None
    b.remover(999)  # inexistente: sem erro


def test_persiste_em_arquivo(tmp_path):
    caminho = str(tmp_path / "bib.sqlite3")
    a = Biblioteca(caminho)
    reg(a)
    a.fechar()
    c = Biblioteca(caminho)
    assert [l.titulo for l in c.listar()] == ["Dom Casmurro"]
    c.fechar()


def test_uso_concorrente_de_varias_threads(b):
    erros = []

    def trabalhar(n):
        try:
            for i in range(20):
                reg(b, f"Livro {n}-{i}", fid=f"{n}-{i}", sha=f"{n}-{i}")
                b.listar("livro")
        except Exception as e:  # pragma: no cover
            erros.append(e)
    ts = [threading.Thread(target=trabalhar, args=(n,)) for n in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert erros == [] and len(b.listar()) == 100
