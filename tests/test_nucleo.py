"""Testes do núcleo: busca, escolha do candidato, download, validação e registro (com fontes e HTTP falsos)."""
import os
import sqlite3
import threading
import time

import pytest

import erros
import nucleo as n
from erros import (Cancelado, DownloadFalhou, ErroFontes, ErroRede, LivroNaoEncontrado)
from helpers import EPUB, PDF, FakeFonte, FakeHttp, arq, contexto, obra

URL = "https://x.test/a.pdf"


def cenario(tmp_path, obras=None, conteudos=None, arquivos=None, **kw):
    fonte = FakeFonte(obras=obras if obras is not None else [obra()],
                      arquivos=arquivos if arquivos is not None else {"1": [arq(URL)]})
    http = FakeHttp(conteudos if conteudos is not None else {URL: PDF})
    return contexto(tmp_path, [fonte], http, **kw), fonte, http


def livros(ctx):
    return sorted(x for x in os.listdir(ctx.pasta) if not x.startswith("."))


class TestProcessar:
    def test_baixa_valida_publica_e_registra(self, tmp_path):
        ctx, _, http = cenario(tmp_path)
        marcas = []
        ctx.progresso = lambda b, t: marcas.append((b, t))
        r = n.processar("dom casmurro", ctx)
        assert r == n.Resultado("Dom Casmurro", "Dom Casmurro - Machado de Assis.pdf", False, "a", "Domínio público")
        assert livros(ctx) == ["Dom Casmurro - Machado de Assis.pdf"]
        assert open(os.path.join(ctx.pasta, r.arquivo), "rb").read() == PDF and marcas == [(len(PDF), len(PDF))]
        reg = ctx.biblioteca.por_fonte("a", "1")
        assert reg.arquivo == r.arquivo and len(reg.sha256) == 64 and reg.tamanho == len(PDF)
        assert os.listdir(os.path.join(ctx.pasta, n.PASTA_PARCIAIS)) == []     # nenhum parcial sobrando

    def test_log_mostra_a_obra_a_fonte_e_a_licenca(self, tmp_path):
        ctx, *_ = cenario(tmp_path)
        n.processar("dom casmurro", ctx)
        texto = "\n".join(m for m, _ in ctx.logs)
        assert "Dom Casmurro — Machado de Assis [Fonte A]" in texto and "Domínio público" in texto

    def test_autor_repetido_no_titulo_nao_duplica_o_nome(self, tmp_path):
        ctx, *_ = cenario(tmp_path, obras=[obra(titulo="Iracema - Romance por José de Alencar", autor="Iracema - Romance")])
        r = n.processar("iracema", ctx)
        assert r.arquivo == "Iracema - Romance por José de Alencar.pdf"

    def test_erro_de_digitacao_usa_busca_alternativa(self, tmp_path):
        fonte = FakeFonte(obras={"murro": [obra()]}, arquivos={"1": [arq(URL)]})
        ctx = contexto(tmp_path, [fonte], FakeHttp({URL: PDF}))
        assert n.processar("cazmurro", ctx).nome == "Dom Casmurro"
        assert fonte.consultas[0] == "cazmurro" and fonte.consultas[-1] == "murro"
        assert any("busca alternativa" in m for m, _ in ctx.logs)

    def test_escolhe_o_melhor_titulo_e_prefere_o_idioma(self, tmp_path):
        obras = [obra(id="1", titulo="Dom Casmurro e Outros Contos", idioma="en"),
                 obra(id="2", titulo="Dom Casmurro", idioma="en"),
                 obra(id="3", titulo="Dom Casmurro", idioma="por")]
        ctx, _, http = cenario(tmp_path, obras=obras, arquivos={i: [arq(f"https://x.test/{i}.pdf")] for i in "123"},
                               conteudos={f"https://x.test/{i}.pdf": PDF for i in "123"})
        assert n.processar("dom casmurro", ctx).nome == "Dom Casmurro"
        assert http.baixados == ["https://x.test/3.pdf"]

    def test_empate_mantem_a_ordem_das_fontes(self, tmp_path):
        a = FakeFonte("a", "A", [obra("a", "1")], {"1": [arq("https://x.test/a.pdf")]})
        b = FakeFonte("b", "B", [obra("b", "1")], {"1": [arq("https://x.test/b.pdf")]})
        ctx = contexto(tmp_path, [a, b], FakeHttp({"https://x.test/a.pdf": PDF, "https://x.test/b.pdf": PDF}))
        n.processar("dom casmurro", ctx)
        assert ctx.http.baixados == ["https://x.test/a.pdf"]

    def test_livro_nao_encontrado(self, tmp_path):
        ctx, _, http = cenario(tmp_path, obras=[obra(titulo="Outro Livro Qualquer")])
        with pytest.raises(LivroNaoEncontrado):
            n.processar("dom casmurro", ctx)
        assert http.baixados == [] and livros(ctx) == []

    def test_jornal_nunca_e_escolhido_nem_baixado(self, tmp_path):
        """Regressão ("tá baixando jornal ainda"): para "1984", qualquer título que contivesse "1984"
        passava da nota mínima; o livro de verdade perdia a vez para diários oficiais e boletins."""
        obras = [obra(id="9", titulo="Haryana Gazette, 1984-09-14, No. 40", autor="Government of Haryana"),
                 obra(id="8", titulo="Jornal O Popular - 25/04/1984 - Capa", autor="Jornal O Popular"),
                 obra(id="1", titulo="1984", autor="George Orwell")]
        ctx, _, http = cenario(tmp_path, obras=obras,
                               arquivos={i: [arq(f"https://x.test/{i}.pdf")] for i in "981"},
                               conteudos={f"https://x.test/{i}.pdf": PDF for i in "981"})
        assert n.processar("1984", ctx).nome == "1984"
        assert http.baixados == ["https://x.test/1.pdf"]

    def test_so_periodicos_nos_resultados_e_livro_nao_encontrado(self, tmp_path):
        obras = [obra(id="9", titulo="Haryana Gazette, 1984-09-14, No. 40", autor="Government of Haryana"),
                 obra(id="8", titulo="Revista Paradoxa 12", autor="Paradoxa Centro de Medios")]
        ctx, _, http = cenario(tmp_path, obras=obras)
        with pytest.raises(LivroNaoEncontrado):
            n.processar("1984", ctx)
        assert http.baixados == [] and livros(ctx) == []

    def test_so_tenta_variantes_se_o_titulo_completo_falhar(self, tmp_path):
        ctx, fonte, _ = cenario(tmp_path)
        n.processar("dom casmurro", ctx)
        assert fonte.consultas == ["dom casmurro"]

    def test_estados_informados(self, tmp_path):
        ctx, *_ = cenario(tmp_path)
        estados = []
        ctx.estado = estados.append
        n.processar("dom casmurro", ctx)
        assert estados[0] == "Buscando" and "Baixando" in estados


class TestFalhas:
    def test_todas_as_fontes_fora_do_ar(self, tmp_path):
        a = FakeFonte("a", "A", erro=ErroRede("sem conexão", transitorio=True))
        b = FakeFonte("b", "B", erro=ErroRede("HTTP 503", status=503))
        ctx = contexto(tmp_path, [a, b])
        with pytest.raises(ErroFontes) as e:
            n.processar("dom casmurro", ctx)
        assert "A: sem conexão" in str(e.value) and "B: HTTP 503" in str(e.value)
        assert len(a.consultas) == 1       # não insiste em todas as variações quando tudo está fora do ar

    def test_uma_fonte_fora_do_ar_nao_impede_a_outra(self, tmp_path):
        a = FakeFonte("a", "A", erro=ErroRede("HTTP 503", status=503))
        b = FakeFonte("b", "B", [obra("b", "1")], {"1": [arq(URL)]})
        ctx = contexto(tmp_path, [a, b], FakeHttp({URL: PDF}))
        assert n.processar("dom casmurro", ctx).fonte == "b"
        assert sum("Fonte indisponível (A)" in m for m, _ in ctx.logs) == 1

    def test_fonte_fora_do_ar_e_outra_sem_resultado_nao_diz_que_o_livro_nao_existe(self, tmp_path):
        """Regressão: "não sei" (fonte caiu) era tratado como "não existe"."""
        a = FakeFonte("a", "A", erro=ErroRede("HTTP 503", status=503))
        b = FakeFonte("b", "B", obras=[])
        ctx = contexto(tmp_path, [a, b])
        with pytest.raises(erros.BuscaIncompleta) as e:
            n.processar("dom casmurro", ctx)
        assert not isinstance(e.value, LivroNaoEncontrado) and "A: HTTP 503" in str(e.value)

    def test_fonte_ok_sem_resultado_continua_sendo_nao_encontrado(self, tmp_path):
        ctx = contexto(tmp_path, [FakeFonte("a", "A", obras=[]), FakeFonte("b", "B", obras=[])])
        with pytest.raises(LivroNaoEncontrado):
            n.processar("dom casmurro", ctx)

    def test_erro_rapido_de_uma_fonte_nao_encurta_o_prazo_da_outra(self, tmp_path):
        """Regressão: a "graça" começava a contar mesmo quando a 1ª resposta era um erro."""
        quebrada = FakeFonte("q", "Quebrada", erro=ErroRede("HTTP 500", status=500))
        lenta = FakeFonte("l", "Lenta", [obra("l", "1")], {"1": [arq(URL)]}, bloquear=threading.Event())
        ctx = contexto(tmp_path, [quebrada, lenta], FakeHttp({URL: PDF}), graca=0.3)
        timer = threading.Timer(0.9, lenta.bloquear.set)
        timer.start()
        try:
            assert n.processar("dom casmurro", ctx).fonte == "l"
        finally:
            timer.cancel()
            lenta.bloquear.set()

    def test_fonte_com_defeito_inesperado_e_isolada(self, tmp_path):
        a = FakeFonte("a", "A", erro=KeyError("campo"))
        b = FakeFonte("b", "B", [obra("b", "1")], {"1": [arq(URL)]})
        ctx = contexto(tmp_path, [a, b], FakeHttp({URL: PDF}))
        assert n.processar("dom casmurro", ctx).fonte == "b"

    def test_fonte_lenta_nao_trava_a_busca(self, tmp_path):
        lenta = FakeFonte("l", "Lenta", bloquear=threading.Event())
        rapida = FakeFonte("r", "Rápida", [obra("r", "1")], {"1": [arq(URL)]})
        ctx = contexto(tmp_path, [lenta, rapida], FakeHttp({URL: PDF}), graca=0.3)
        inicio = time.monotonic()
        try:
            assert n.processar("dom casmurro", ctx).fonte == "r"
        finally:
            lenta.bloquear.set()
        assert time.monotonic() - inicio < 5 and any("Lenta" in m and "demorou" in m for m, _ in ctx.logs)

    def test_download_com_conteudo_invalido_vira_downloadfalhou(self, tmp_path):
        ctx, _, _ = cenario(tmp_path, conteudos={URL: b"<html>erro</html>"})
        with pytest.raises(DownloadFalhou) as e:
            n.processar("dom casmurro", ctx)
        assert e.value.nome == "Dom Casmurro" and "PDF válido" in e.value.motivo
        assert livros(ctx) == [] and ctx.biblioteca.listar() == []
        assert os.listdir(os.path.join(ctx.pasta, n.PASTA_PARCIAIS)) == []

    def test_executavel_e_recusado_e_apagado(self, tmp_path):
        ctx, _, _ = cenario(tmp_path, arquivos={"1": [arq("https://x.test/a.exe", ".exe")]},
                            conteudos={"https://x.test/a.exe": b"MZ" + b"\0" * 100})
        with pytest.raises(DownloadFalhou, match="não permitido"):
            n.processar("dom casmurro", ctx)
        assert livros(ctx) == [] and os.listdir(os.path.join(ctx.pasta, n.PASTA_PARCIAIS)) == []

    def test_tenta_o_proximo_formato(self, tmp_path):
        ctx, _, http = cenario(tmp_path, arquivos={"1": [arq("https://x.test/a.pdf"), arq("https://x.test/a.epub", ".epub")]},
                               conteudos={"https://x.test/a.pdf": ErroRede("HTTP 500", status=500), "https://x.test/a.epub": EPUB})
        assert n.processar("dom casmurro", ctx).arquivo.endswith(".epub")
        assert http.baixados == ["https://x.test/a.pdf", "https://x.test/a.epub"]

    def test_tenta_a_proxima_obra(self, tmp_path):
        obras = [obra(id="1", titulo="Dom Casmurro"), obra(id="2", titulo="Dom Casmurro 2")]
        ctx, _, http = cenario(tmp_path, obras=obras,
                               arquivos={"1": [], "2": [arq("https://x.test/2.pdf")]},
                               conteudos={"https://x.test/2.pdf": PDF})
        assert n.processar("dom casmurro", ctx).nome == "Dom Casmurro 2"
        assert any("nenhum arquivo de livro" in m for m, _ in ctx.logs)

    def test_erro_ao_listar_arquivos_conta_como_falha(self, tmp_path):
        ctx, *_ = cenario(tmp_path, arquivos={"1": ErroRede("HTTP 500", status=500)})
        with pytest.raises(DownloadFalhou, match="HTTP 500"):
            n.processar("dom casmurro", ctx)

    def test_limita_candidatos_tentados(self, tmp_path):
        obras = [obra(id=str(i), titulo=f"Dom Casmurro {i}") for i in range(1, 9)]
        ctx, _, http = cenario(tmp_path, obras=obras, arquivos={str(i): [] for i in range(1, 9)})
        with pytest.raises(DownloadFalhou):
            n.processar("dom casmurro", ctx)
        assert sum("nenhum arquivo" in m for m, _ in ctx.logs) == n.MAX_CANDIDATOS


class TestDuplicatas:
    def test_mesma_obra_na_biblioteca_nao_baixa_de_novo(self, tmp_path):
        ctx, _, http = cenario(tmp_path)
        n.processar("dom casmurro", ctx)
        r = n.processar("dom casmurro", ctx)
        assert r.ja_existia and http.baixados == [URL] and len(livros(ctx)) == 1

    def test_registro_sem_arquivo_no_disco_baixa_de_novo(self, tmp_path):
        ctx, _, http = cenario(tmp_path)
        r = n.processar("dom casmurro", ctx)
        os.remove(os.path.join(ctx.pasta, r.arquivo))
        assert not n.processar("dom casmurro", ctx).ja_existia and len(http.baixados) == 2

    def test_mesmo_conteudo_em_outra_obra_nao_duplica_o_arquivo(self, tmp_path):
        obras = [obra(id="1", titulo="Dom Casmurro"), obra(id="2", titulo="Dom Casmurro - 2a edicao")]
        ctx, _, http = cenario(tmp_path, obras=[obras[0]],
                               arquivos={"1": [arq("https://x.test/1.pdf")], "2": [arq("https://x.test/2.pdf")]},
                               conteudos={"https://x.test/1.pdf": PDF, "https://x.test/2.pdf": PDF})
        n.processar("dom casmurro", ctx)
        ctx.fontes[0]._obras = [obras[1]]          # agora só a obra 2 aparece, com o MESMO conteúdo da obra 1
        r = n.processar("dom casmurro", ctx)
        assert r.ja_existia and len(livros(ctx)) == 1

    def test_dois_pedidos_simultaneos_da_mesma_obra_baixam_uma_vez(self, tmp_path):
        ctx, _, http = cenario(tmp_path)
        http.antes = lambda url, c: time.sleep(0.3)
        resultados, erros = [], []

        def rodar():
            try:
                resultados.append(n.processar("dom casmurro", ctx))
            except Exception as e:  # pragma: no cover
                erros.append(e)
        ts = [threading.Thread(target=rodar) for _ in range(3)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        assert erros == [] and http.baixados == [URL] and len(livros(ctx)) == 1
        assert sorted(r.ja_existia for r in resultados) == [False, True, True]


class TestRegistro:
    def test_falha_ao_registrar_nao_deixa_arquivo_orfao(self, tmp_path, monkeypatch):
        """Regressão: o arquivo era publicado e, se o banco falhasse, ficava no disco sem registro
        (e a próxima tentativa criava "Livro (2).pdf")."""
        ctx, *_ = cenario(tmp_path)

        def quebra(**kw):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(ctx.biblioteca, "registrar", quebra)
        with pytest.raises(DownloadFalhou) as e:
            n.processar("dom casmurro", ctx)
        assert "registrar" in e.value.motivo
        assert livros(ctx) == [] and os.listdir(os.path.join(ctx.pasta, n.PASTA_PARCIAIS)) == []


class TestCancelamento:
    def test_cancelado_antes_de_comecar(self, tmp_path):
        ctx, fonte, http = cenario(tmp_path)
        with pytest.raises(Cancelado):
            n.processar("dom casmurro", ctx, cancelado=lambda: True)
        assert http.baixados == [] and ctx.biblioteca.listar() == []

    def test_cancelado_durante_o_download_nao_publica_nada(self, tmp_path):
        ctx, _, http = cenario(tmp_path)
        estado = {"baixando": False}

        def cancela(url, cancelado):
            estado["baixando"] = True
            if cancelado():
                raise Cancelado()
        http.antes = cancela
        with pytest.raises(Cancelado):
            n.processar("dom casmurro", ctx, cancelado=lambda: estado["baixando"])
        assert livros(ctx) == [] and ctx.biblioteca.listar() == []

    def test_cancelado_dentro_das_fontes_e_cancelado_nao_falha_de_fonte(self, tmp_path):
        """Regressão: Parar durante a busca virava "As fontes não responderam: A: ; B: "."""
        fontes = [FakeFonte("a", "A", erro=Cancelado()), FakeFonte("b", "B", erro=Cancelado())]
        ctx = contexto(tmp_path, fontes)
        with pytest.raises(Cancelado):
            n.processar("dom casmurro", ctx)

    def test_cancelar_a_busca_interrompe_fonte_travada(self, tmp_path):
        trava = FakeFonte("t", "Travada", bloquear=threading.Event())
        ctx = contexto(tmp_path, [trava])
        marca = {"c": False}
        threading.Timer(0.2, lambda: marca.update(c=True)).start()
        inicio = time.monotonic()
        with pytest.raises(Cancelado):
            n.processar("dom casmurro", ctx, cancelado=lambda: marca["c"])
        trava.bloquear.set()
        assert time.monotonic() - inicio < 3


class TestParciais:
    def test_limpa_so_os_antigos(self, tmp_path):
        base = tmp_path / n.PASTA_PARCIAIS
        base.mkdir()
        velho, novo = base / "velho.part", base / "novo.part"
        velho.write_bytes(b"x")
        novo.write_bytes(b"x")
        os.utime(velho, (time.time() - 10 * 86400,) * 2)
        n.limpar_parciais_antigos(str(tmp_path))
        assert not velho.exists() and novo.exists()

    def test_pasta_inexistente_nao_da_erro(self, tmp_path):
        n.limpar_parciais_antigos(str(tmp_path / "nao-existe"))
