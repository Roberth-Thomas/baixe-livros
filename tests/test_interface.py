"""Testes da janela (tkinter) e da linha de comando, com fontes e HTTP falsos."""
import os
import sys
import threading
import time

import pytest

import baixar_livro as b
import fila as f
from helpers import PDF, FakeFonte, FakeHttp, arq, contexto, obra


def ctx_falso(tmp_path, titulos=("Dom Casmurro", "Iracema", "Quincas Borba"), http=None):
    obras, arquivos, conteudos = [], {}, {}
    for i, t in enumerate(titulos, 1):
        obras.append(obra(id=str(i), titulo=t, autor="Autor"))
        url = f"https://x.test/{i}.pdf"
        arquivos[str(i)], conteudos[url] = [arq(url)], PDF + str(i).encode()
    http = http or FakeHttp(conteudos)
    http.conteudos = {**conteudos, **http.conteudos}
    return contexto(tmp_path, [FakeFonte(obras=obras, arquivos=arquivos)], http)


def esperar(app, cond, seg=10):
    fim = time.time() + seg
    while time.time() < fim and not cond():
        app.update()
        time.sleep(0.02)
    app.update()
    return cond()


@pytest.fixture
def app(tmp_path, monkeypatch):
    import tkinter
    a, erro, ctx = None, None, ctx_falso(tmp_path)
    for _ in range(5):  # o Tcl do Windows falha de vez em quando ao criar muitas janelas em sequência
        try:
            a = b.App(ctx)
            break
        except tkinter.TclError as e:
            erro = e
            time.sleep(0.5)
    if a is None:
        pytest.skip(f"sem ambiente gráfico: {erro}")
    a.popups, a.avisos, a.abertos = [], [], []
    for nome in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(b.messagebox, nome, lambda *x, n=nome, **k: a.popups.append(n))
    monkeypatch.setattr(b.messagebox, "askyesno", lambda *x, **k: a.resposta)
    a.resposta = True
    monkeypatch.setattr(b.os, "startfile", lambda p: a.abertos.append(p), raising=False)
    original = a.avisar
    monkeypatch.setattr(a, "avisar", lambda: a.avisos.append(1))
    a.original_avisar = original
    yield a
    try:
        a._encerrando = True
        a.fila.parar_tudo()
        a.destroy()
    except Exception:
        pass


def digitar(app, *titulos):
    app.entrada.insert("1.0", "\n".join(titulos))
    app.adicionar()


def estados(app):
    return [app.tabela.item(i, "values")[3] for i in app.tabela.get_children()]


def tags_do_log(app, tag):
    r = app.log.tag_ranges(tag)
    return [app.log.get(str(i), str(j)).strip() for i, j in zip(r[::2], r[1::2])]


class TestDownloads:
    def test_titulo_concluido_aparece_na_tabela_na_biblioteca_e_em_verde_no_log(self, app):
        digitar(app, "dom casmurro")
        assert app.entrada.get("1.0", "end").strip() == ""            # a caixa é limpa
        assert esperar(app, lambda: estados(app) == [f.CONCLUIDO])
        assert any("Concluído" in t and "Dom Casmurro" in t for t in tags_do_log(app, "ok"))
        assert app.popups == [] and app.total_lbl.cget("text") == "1 livro(s)"
        assert app.tabela.item("1", "values")[4] == "100%"

    def test_varios_titulos_em_lote_e_um_unico_aviso_no_fim(self, app):
        digitar(app, "dom casmurro", "iracema", "quincas borba")
        assert esperar(app, lambda: estados(app) == [f.CONCLUIDO] * 3)
        assert esperar(app, lambda: len(app.avisos) == 1)
        time.sleep(0.3)
        app.update()
        assert len(app.avisos) == 1 and app.total_lbl.cget("text") == "3 livro(s)"

    def test_nao_encontrado_em_vermelho_no_log_sem_popup(self, app):
        digitar(app, "livro que nao existe")
        assert esperar(app, lambda: estados(app) == [f.NAO_ENCONTRADO])
        assert any("Livro não encontrado" in t and "livro que nao existe" in t for t in tags_do_log(app, "erro"))
        assert app.popups == []

    def test_log_com_varios_itens_traz_o_prefixo_do_item(self, app):
        digitar(app, "dom casmurro", "iracema")
        assert esperar(app, lambda: estados(app) == [f.CONCLUIDO] * 2)
        assert "[1] " in app.log.get("1.0", "end") and "[2] " in app.log.get("1.0", "end")

    def test_entrada_vazia_nao_faz_nada(self, app):
        digitar(app, "   ", "")
        assert app.fila.itens == []

    def test_parar_tudo(self, app):
        segurar = threading.Event()

        def antes(url, cancelado):
            while not cancelado() and not segurar.is_set():
                time.sleep(0.02)
            if cancelado():
                from erros import Cancelado
                raise Cancelado()
        app.ctx.http.antes = antes
        digitar(app, "dom casmurro")
        assert esperar(app, lambda: estados(app) == [f.BAIXANDO])
        assert esperar(app, lambda: app.botao_parar.instate(["!disabled"]))
        app.botao_parar.invoke()
        assert esperar(app, lambda: estados(app) == [f.PARADO])
        assert esperar(app, lambda: app.botao_parar.instate(["disabled"]))
        assert "Busca interrompida" in app.log.get("1.0", "end") and tags_do_log(app, "ok") == [] == tags_do_log(app, "erro")
        assert app.avisos == []          # parou de propósito: sem aviso sonoro

    def test_repetir_falhas(self, app):
        app.ctx.http.conteudos["https://x.test/1.pdf"] = b"<html>erro</html>"
        digitar(app, "dom casmurro")
        assert esperar(app, lambda: estados(app) == [f.FALHOU])
        app.ctx.http.conteudos["https://x.test/1.pdf"] = PDF
        app.botao_repetir.invoke()
        assert esperar(app, lambda: estados(app) == [f.CONCLUIDO])

    def test_aviso_real_nao_quebra(self, app):
        app.original_avisar()   # som + janela para frente: não pode levantar exceção


class TestBiblioteca:
    def concluir(self, app):
        digitar(app, "dom casmurro", "iracema")
        assert esperar(app, lambda: estados(app) == [f.CONCLUIDO] * 2)

    def test_lista_e_filtra(self, app):
        self.concluir(app)
        assert len(app.lista.get_children()) == 2
        app.filtro.set("IRACEMA")
        app.update()
        assert [app.lista.item(i, "values")[0] for i in app.lista.get_children()] == ["Iracema"]
        assert app.total_lbl.cget("text") == "1 livro(s)"
        app.filtro.set("nada disso")
        app.update()
        assert app.lista.get_children() == ()

    def test_abrir_livro_so_documentos_dentro_da_pasta(self, app):
        self.concluir(app)
        app.lista.selection_set("1")
        app.abrir_livro()
        assert len(app.abertos) == 1 and app.abertos[0].startswith(app.pasta) and app.abertos[0].endswith(".pdf")

    def test_abrir_nao_executa_tipos_perigosos(self, app, tmp_path):
        for nome in ("x.exe", "x.zip", "x.rar", "x.bat"):
            (tmp_path / nome).write_bytes(b"MZ")
            app._abrir_arquivo(str(tmp_path / nome))
        assert app.abertos == []

    def test_registro_com_caminho_fora_da_pasta_nao_abre_nada(self, app, tmp_path):
        fora = tmp_path / "segredo.pdf"
        fora.write_bytes(PDF)
        app.ctx.biblioteca.registrar(titulo="Falso", autor="", fonte="x", fonte_id="1", arquivo="../segredo.pdf",
                                     sha256="z", tamanho=1)
        app.atualizar_biblioteca()
        app.lista.selection_set(app.lista.get_children()[0])
        app.abrir_livro()
        assert app.abertos == []

    def test_remover_do_registro_nao_apaga_o_arquivo(self, app):
        self.concluir(app)
        arquivo = os.path.join(app.pasta, app.ctx.biblioteca.por_fonte("a", "1").arquivo)
        app.lista.selection_set("1")
        app.resposta = False
        app.remover_registro()
        assert len(app.ctx.biblioteca.listar()) == 2
        app.resposta = True
        app.remover_registro()
        assert len(app.ctx.biblioteca.listar()) == 1 and os.path.exists(arquivo)
        assert app.total_lbl.cget("text") == "1 livro(s)"

    def test_sem_selecao_nao_faz_nada(self, app):
        app.abrir_livro()
        app.remover_registro()
        assert app.abertos == []


class TestRobustez:
    def test_erro_ao_exibir_um_evento_nao_para_a_fila(self, app, monkeypatch):
        agendados = []
        monkeypatch.setattr(app, "after", lambda ms, fn=None, *a: agendados.append(fn))
        monkeypatch.setattr(app, "_atualizar_item", lambda item: (_ for _ in ()).throw(RuntimeError("tcl quebrou")))
        app.eventos.put(("item", f.Item(1, "x")))
        app.eventos.put(("log", f.Item(1, "x"), "mensagem seguinte", None))
        app.processar_eventos()
        assert agendados == [app.processar_eventos]
        texto = app.log.get("1.0", "end")
        assert "ERRO INTERNO" in texto and "mensagem seguinte" in texto

    def test_fechar_janela_para_a_fila_fecha_o_banco_e_nao_reagenda(self, app, monkeypatch):
        mortos, agendados = [], []
        monkeypatch.setattr(app, "destroy", lambda: mortos.append("destroy"))
        digitar(app, "dom casmurro")
        esperar(app, lambda: estados(app) != [])
        app.fechar_janela()
        assert mortos == ["destroy"] and app.fila.ativos() == 0
        monkeypatch.setattr(app, "after", lambda *a: agendados.append(a))
        app.processar_eventos()
        assert agendados == []
        with pytest.raises(Exception):
            app.ctx.biblioteca.listar()          # conexão fechada
        monkeypatch.undo()


class TestEncerramento:
    def test_fechar_janela_nao_fecha_o_banco_se_a_fila_ainda_trabalha(self, app, monkeypatch):
        """Regressão: o banco era fechado após 3 s mesmo com download em andamento; o arquivo era
        publicado e o registro falhava."""
        monkeypatch.setattr(app, "destroy", lambda: None)
        monkeypatch.setattr(app.fila, "aguardar", lambda timeout=None: False)
        app.fechar_janela()
        assert app.ctx.biblioteca.listar() == []        # a conexão continua aberta
        monkeypatch.undo()


class TestFormatacao:
    def test_tamanho(self):
        assert b.formatar_tamanho(None) == "" and b.formatar_tamanho(0) == ""
        assert b.formatar_tamanho(2048) == "2 KB" and b.formatar_tamanho(31346440) == "29.9 MB"

    def test_progresso(self):
        assert b.formatar_progresso(f.Item(1, "x", f.BAIXANDO, 500_000, 1_000_000)) == "50% (488 KB)"
        assert b.formatar_progresso(f.Item(1, "x", f.BAIXANDO, 3 * 1024 * 1024, None)) == "3.0 MB"
        assert b.formatar_progresso(f.Item(1, "x", f.CONCLUIDO)) == "100%"
        assert b.formatar_progresso(f.Item(1, "x", f.NA_FILA)) == ""


class TestCLI:
    def test_todos_concluidos_retorna_zero(self, tmp_path):
        saida = []
        assert b.executar_cli(["dom casmurro", "iracema"], ctx_falso(tmp_path), 2, saida.append) == 0
        assert sum("[OK] ✔ Concluído" in s for s in saida) == 2

    def test_qualquer_falha_retorna_um(self, tmp_path):
        saida = []
        assert b.executar_cli(["dom casmurro", "nao existe"], ctx_falso(tmp_path), 2, saida.append) == 1
        assert any("[ERRO] ✖ Livro não encontrado: “nao existe”" in s for s in saida)

    def test_terminal_sem_suporte_a_unicode_nao_derruba_o_download(self, tmp_path):
        """Regressão: print de '✔' em cp1252 levantava UnicodeEncodeError dentro da thread de trabalho."""
        impresso = []

        def terminal_antigo(linha):
            linha.encode("cp1252")        # levanta UnicodeEncodeError para ✔ / ✖ / “ ”
            impresso.append(linha)
        assert b.executar_cli(["dom casmurro", "nao existe"], ctx_falso(tmp_path), 2, terminal_antigo) == 1
        assert any(s.startswith("[1] [OK] ") and "Conclu" in s for s in impresso)    # ✔ virou "?"
        assert any("[ERRO] " in s and "Livro n" in s for s in impresso)

    def test_sem_titulos_nao_e_sucesso(self, tmp_path):
        assert b.executar_cli([], ctx_falso(tmp_path), 1, lambda s: None) == 1

    def test_main_usa_pasta_e_paralelo(self, tmp_path, monkeypatch):
        usados = {}
        ctx = ctx_falso(tmp_path)
        monkeypatch.setattr(b, "criar_contexto", lambda pasta: usados.setdefault("pasta", pasta) and ctx)
        assert b.main(["--pasta", str(tmp_path / "x"), "--paralelo", "2", "dom casmurro"]) == 0
        assert usados["pasta"] == str(tmp_path / "x")

    @pytest.mark.parametrize("args", [["--paralelo", "0", "x"], ["--paralelo", "9", "x"], ["--naoexiste"]])
    def test_argumentos_invalidos(self, args):
        with pytest.raises(SystemExit) as e:
            b.main(args)
        assert e.value.code == 2


class TestCaminhos:
    def test_pasta_base_como_exe(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", r"C:\Apps\BaixeLivros\BaixeLivros.exe")
        assert b.pasta_base() == r"C:\Apps\BaixeLivros"

    def test_biblioteca_fica_na_pasta_dos_livros(self, tmp_path):
        pasta = tmp_path / "meus livros"
        ctx = b.criar_contexto(str(pasta))
        try:
            ctx.biblioteca.registrar(titulo="A", autor="", fonte="x", fonte_id="1", arquivo="a.pdf", sha256="z", tamanho=1)
            assert (pasta / b.BANCO_NOME).exists()
        finally:
            ctx.biblioteca.fechar()
