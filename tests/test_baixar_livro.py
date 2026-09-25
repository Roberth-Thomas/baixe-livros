"""Testes do Baixe Livros. `pytest` roda os unitários; `pytest -m integration` usa o site real."""
import os
import threading
import time

import pytest
from selenium.common.exceptions import TimeoutException, WebDriverException

import baixar_livro as b

PDF = b"%PDF-1.4\n% conteudo de teste\n"


# --------------------------------------------------------------------------- #
# comparação de títulos
# --------------------------------------------------------------------------- #
class TestTokens:
    def test_remove_acentos_pontuacao_e_caixa(self):
        assert b.tokens("Memórias Póstumas de Brás Cubas!") == ["memorias", "postumas", "de", "bras", "cubas"]

    def test_letras_especiais_nao_quebram_a_palavra(self):
        assert b.tokens("Søren Łukasz Æsop") == ["soren", "lukasz", "aesop"]

    def test_outros_alfabetos_sao_mantidos(self):
        assert b.tokens("Война и мир") == ["война", "и", "мир"]


class TestPontuar:
    @pytest.mark.parametrize("digitado,titulo", [
        ("ads", "Facebook Ads: O Guia Definitivo"),
        ("dom casmuro", "Dom Casmurro – Machado de Assis"),
        ("cazmurro", "Dom Casmurro"),
        ("orgulho e preconseito", "Orgulho e Preconceito – Jane Austen"),
        ("memorias postumaz", "Memórias Póstumas de Brás Cubas"),
        ("quincas", "Quincas Borba"),
        ("CASA", "Casa Grande"),
    ])
    def test_aceita_titulos_compativeis(self, digitado, titulo):
        assert b.pontuar(digitado, titulo) >= b.MIN_NOTA

    @pytest.mark.parametrize("digitado,titulo", [
        ("ads", "Dom Casmurro"),
        ("ads", "Didascalicon (ou A Arte de Ler)"),
        ("amor", "Amar e Ser Amado"),        # palavra de 4 letras: só idêntica
        ("casa", "O Caso da Vara"),
        ("cazmurro", "Murro"),
        ("xyzabc", "Dom Casmurro"),
        ("", "Dom Casmurro"),
    ])
    def test_rejeita_titulos_incompativeis(self, digitado, titulo):
        assert b.pontuar(digitado, titulo) == 0.0

    def test_numero_e_palavra_curta_nao_casam_com_o_contexto(self):
        # regressão: o cartão inteiro (datas, "de") não pode servir de evidência
        assert b.pontuar("Duna 2", "Duna Messias", "Publicado em 2 de março de 2023 Frank Herbert") == 0.0
        assert b.pontuar("Rei Leao", "Leao Verde", "Rei do Nada") == 0.0

    def test_autor_no_contexto_ajuda_palavras_longas(self):
        assert b.pontuar("dom casmurro machado de assis", "Dom Casmurro", "Machado de Assis · 2019") >= b.MIN_NOTA

    def test_so_o_autor_nao_basta(self):
        assert b.pontuar("machado de assis", "Dom Casmurro", "Machado de Assis · 2019") == 0.0

    def test_sufixo_apos_travessao_nao_penaliza(self):
        # o site nomeia "Título – Autor": o autor não pode rebaixar o título exato
        assert b.pontuar("dom casmurro", "Dom Casmurro – Machado de Assis") == b.pontuar("dom casmurro", "Dom Casmurro")

    def test_titulo_exato_vence_titulo_mais_longo(self):
        exato = b.pontuar("dom casmurro", "Dom Casmurro – Machado de Assis")
        longo = b.pontuar("dom casmurro", "Dom Casmurro e Outros Contos – Machado de Assis")
        assert exato > longo >= b.MIN_NOTA

    def test_titulo_principal(self):
        assert b.titulo_principal("Dom Casmurro – Machado de Assis") == "Dom Casmurro"
        assert b.titulo_principal("Guerra e Paz") == "Guerra e Paz"


class TestOrdenarCandidatos:
    def test_ordena_pela_nota_e_empate_pela_ordem_do_site(self):
        r = [("Dom Casmurro e Outros Contos – Machado de Assis", "https://www.baixelivros.com.br/a", ""),
             ("Dom Casmurro – Machado de Assis", "https://www.baixelivros.com.br/b", ""),
             ("Dom Casmurro – Machado de Assis", "https://www.baixelivros.com.br/c", "")]
        urls = [c.url for c in b.ordenar_candidatos("dom casmurro", r, set())]
        assert urls == ["https://www.baixelivros.com.br/b", "https://www.baixelivros.com.br/c",
                        "https://www.baixelivros.com.br/a"]

    def test_ignora_tentados_incompativeis_e_hosts_de_fora(self):
        r = [("Dom Casmurro", "https://www.baixelivros.com.br/ok", ""),
             ("Dom Casmurro", "https://evil.example/dom", ""),
             ("Dom Casmurro", "http://www.baixelivros.com.br/http", ""),
             ("Dom Casmurro", "https://www.baixelivros.com.br/ja", ""),
             ("Outro Livro", "https://www.baixelivros.com.br/outro", "")]
        cands = b.ordenar_candidatos("dom casmurro", r, {"https://www.baixelivros.com.br/ja"})
        assert [c.url for c in cands] == ["https://www.baixelivros.com.br/ok"]


class TestVariantesBusca:
    def test_titulo_sempre_primeiro(self):
        assert next(b.variantes_busca("dom casmurro")) == "dom casmurro"

    def test_titulo_sem_letras_latinas_ainda_gera_busca(self):
        assert list(b.variantes_busca("Война и мир"))[0] == "Война и мир"
        assert list(b.variantes_busca("???"))[0] == "???"

    def test_inclui_prefixos_e_sufixos(self):
        v = list(b.variantes_busca("cazmurro"))
        assert "cazmurr" in v and "murro" in v and "azmurro" in v

    def test_sem_duplicatas_e_com_limite(self):
        v = list(b.variantes_busca("dom casmurro machado de assis"))
        assert len({" ".join(b.tokens(x)) for x in v}) == len(v)
        assert len(v) <= b.MAX_VARIANTES + 1

    def test_do_trecho_maior_para_o_menor(self):
        v = list(b.variantes_busca("preconseito"))[1:]
        assert [len(x) for x in v] == sorted((len(x) for x in v), reverse=True)


# --------------------------------------------------------------------------- #
# segurança de URLs e arquivos
# --------------------------------------------------------------------------- #
class TestUrls:
    @pytest.mark.parametrize("url", [
        "https://www.baixelivros.com.br/literatura-brasileira/dom-casmurro",
        "HTTPS://WWW.BAIXELIVROS.COM.BR/x",
    ])
    def test_pagina_permitida(self, url):
        assert b.url_permitida(url)

    @pytest.mark.parametrize("url", [
        "", None, "http://www.baixelivros.com.br/x", "https://evil.example/x",
        "https://www.baixelivros.com.br.evil.example/x", "file:///C:/Users/x/secret.pdf",
        "javascript:alert(1)", "//www.baixelivros.com.br/x",
    ])
    def test_pagina_negada(self, url):
        assert not b.url_permitida(url)

    def test_arquivo_permitido(self):
        assert b.url_arquivo_permitida("https://cdn.example.com/ugd/abc.pdf")
        assert b.url_arquivo_permitida("https://cdn.example.com/a/B.EPUB?x=1")

    @pytest.mark.parametrize("url", [
        "file:///C:/Users/x/secret.pdf", "javascript:alert(1)//.pdf", "http://cdn.example.com/a.pdf",
        "https://evil.example/a.exe?x=.pdf", "https://evil.example/a.exe#.zip", "https://evil.example/a.exe", "",
    ])
    def test_arquivo_negado(self, url):
        assert not b.url_arquivo_permitida(url)


class TestNomeSeguro:
    def test_caracteres_invalidos_e_path_traversal(self):
        assert b.nome_seguro("../../x") == "x"
        assert b.nome_seguro('A:B*C?"D"<E>|F') == "A B C D E F"
        assert "/" not in b.nome_seguro("a/b\\c")

    def test_reticencias_e_pontos_finais(self):
        assert b.nome_seguro("Didática Magna…") == "Didática Magna"
        assert b.nome_seguro("Aux.") == "_Aux"

    @pytest.mark.parametrize("nome", ["CON", "nul", "com1", "LPT9", "Con.txt"])
    def test_nomes_reservados_do_windows(self, nome):
        assert b.nome_seguro(nome).startswith("_")

    def test_vazio_vira_livro(self):
        assert b.nome_seguro("...") == "livro"

    def test_limite_de_tamanho_sem_espaco_final(self):
        nome = b.nome_seguro(("a" * 118) + " b " + "c" * 50)
        assert len(nome) <= 120 and nome == nome.rstrip(" .")

    def test_respeita_o_tamanho_do_caminho(self):
        pasta = "C:\\" + "x" * 200
        assert len(pasta) + len(b.nome_seguro("a" * 200, pasta, ".pdf")) + 20 <= 260


class TestArquivos:
    def test_ja_existe_so_considera_livros_reais(self, tmp_path):
        (tmp_path / "Livro X.jpg").write_bytes(b"x")
        (tmp_path / "Livro Y").mkdir()
        (tmp_path / "Livro Z.pdf").write_bytes(PDF)
        assert b.ja_existe(str(tmp_path), "Livro X") is None
        assert b.ja_existe(str(tmp_path), "Livro Y") is None
        assert b.ja_existe(str(tmp_path), "livro z") == "Livro Z.pdf"

    def test_validar_arquivo(self, tmp_path):
        ok = tmp_path / "a.pdf"
        ok.write_bytes(PDF)
        b.validar_arquivo(str(ok))
        vazio = tmp_path / "v.pdf"
        vazio.write_bytes(b"")
        falso = tmp_path / "f.pdf"
        falso.write_bytes(b"<html>erro</html>")
        exe = tmp_path / "x.exe"
        exe.write_bytes(b"MZ")
        for arq, exc in [(vazio, b.FalhaDownload), (falso, b.FalhaDownload), (exe, b.TipoNaoPermitido)]:
            with pytest.raises(exc):
                b.validar_arquivo(str(arq))

    def test_publicar_renomeia_para_o_titulo(self, tmp_path):
        origem = tmp_path / "5ca0e9_xyz.pdf"
        origem.write_bytes(PDF)
        nome = b.publicar(str(origem), str(tmp_path), "Dom Casmurro – Machado de Assis", lambda m: None)
        assert nome == "Dom Casmurro – Machado de Assis.pdf" and (tmp_path / nome).exists() and not origem.exists()

    def test_publicar_nao_sobrescreve(self, tmp_path):
        (tmp_path / "Livro.pdf").write_bytes(b"antigo")
        origem = tmp_path / "novo.pdf"
        origem.write_bytes(PDF)
        assert b.publicar(str(origem), str(tmp_path), "Livro", lambda m: None) == "Livro (2).pdf"
        assert (tmp_path / "Livro.pdf").read_bytes() == b"antigo"

    def test_publicar_titulo_reservado_nao_quebra(self, tmp_path):
        origem = tmp_path / "dl.pdf"
        origem.write_bytes(PDF)
        assert b.publicar(str(origem), str(tmp_path), "NUL", lambda m: None) == "_NUL.pdf"

    def test_publicar_com_oserror_mantem_nome_original_e_avisa(self, tmp_path, monkeypatch):
        origem = tmp_path / "original.pdf"
        origem.write_bytes(PDF)
        real, chamadas, logs = os.rename, [], []

        def rename(a, c):
            chamadas.append(c)
            if len(chamadas) == 1:
                raise OSError("nome inválido")
            return real(a, c)
        monkeypatch.setattr(b.os, "rename", rename)
        assert b.publicar(str(origem), str(tmp_path), "Livro", logs.append) == "original.pdf"
        assert any("Aviso" in m for m in logs) and (tmp_path / "original.pdf").exists()

    def test_remover_trabalho_e_varredura(self, tmp_path):
        t = tmp_path / (b.PREFIXO_TMP + "abc")
        (t / "sub").mkdir(parents=True)
        (t / "sub" / "x.crdownload").write_bytes(b"x")
        b.remover_trabalho(str(t), lambda m: None)
        assert not t.exists()
        velho = tmp_path / (b.PREFIXO_TMP + "velho")
        velho.mkdir()
        novo = tmp_path / (b.PREFIXO_TMP + "novo")
        novo.mkdir()
        outro = tmp_path / "Minha Pasta"
        outro.mkdir()
        os.utime(velho, (time.time() - 7200,) * 2)
        b.varrer_temporarios(str(tmp_path))
        assert not velho.exists() and novo.exists() and outro.exists()


class TestAguardarDownload:
    @staticmethod
    def nunca():
        return False

    def test_retorna_livro_completo_e_ignora_downloads_htm(self, tmp_path):
        (tmp_path / "downloads.htm").write_text("x")
        (tmp_path / "livro.pdf").write_bytes(PDF)
        assert b.aguardar_download(str(tmp_path), self.nunca, inicio_max=1) == "livro.pdf"

    def test_espera_o_crdownload_terminar(self, tmp_path):
        (tmp_path / "Unconfirmed 1.crdownload").write_bytes(b"...")

        def terminar():
            time.sleep(0.5)
            (tmp_path / "Unconfirmed 1.crdownload").rename(tmp_path / "livro.pdf")
        threading.Thread(target=terminar).start()
        assert b.aguardar_download(str(tmp_path), self.nunca, inicio_max=1, timeout=10) == "livro.pdf"

    def test_falha_se_nada_comecar(self, tmp_path):
        with pytest.raises(b.FalhaDownload, match="não começou"):
            b.aguardar_download(str(tmp_path), self.nunca, inicio_max=0.3)

    def test_falha_se_demorar_demais_depois_de_comecar(self, tmp_path):
        (tmp_path / "x.crdownload").write_bytes(b"...")
        with pytest.raises(b.FalhaDownload, match="demorou"):
            b.aguardar_download(str(tmp_path), self.nunca, inicio_max=5, timeout=0.5)

    @pytest.mark.parametrize("nome", ["setup.exe", "run.bat", "atalho.lnk", "semextensao"])
    def test_tipo_nao_permitido(self, tmp_path, nome):
        (tmp_path / nome).write_bytes(b"x")
        with pytest.raises(b.TipoNaoPermitido):
            b.aguardar_download(str(tmp_path), self.nunca, inicio_max=1)

    def test_respeita_o_cancelamento(self, tmp_path):
        with pytest.raises(b.Cancelado):
            b.aguardar_download(str(tmp_path), lambda: True, inicio_max=5)


# --------------------------------------------------------------------------- #
# navegador (drivers falsos)
# --------------------------------------------------------------------------- #
class Elem:
    def __init__(self, texto="", attrs=None, filhos=None, container=None):
        self.texto, self.attrs, self.filhos, self.container = texto, attrs or {}, filhos or {}, container

    def get_attribute(self, nome):
        return self.texto if nome == "textContent" else self.attrs.get(nome)

    def find_elements(self, by, sel):
        return self.filhos.get(sel, [])

    def find_element(self, by, sel):
        return self.container or self


def item(nome, href, meta=""):
    return Elem(filhos={"h3 a": [Elem(nome, {"href": href})], ".bl-rs__meta": [Elem(meta)] if meta else []})


class DriverBusca:
    """Simula a página /search do site."""
    title = "Encontre a sua próxima leitura | Baixe Livros"

    def __init__(self, total="3 livros encontrados", itens=(), sugestao=None, proxima=False, sem_marcador=False,
                 erro_get=None):
        cab = Elem(f"Resultados {total} " + (f"Você quis dizer: {sugestao}?" if sugestao else ""))
        self.dom = {".bl-bg-resultados__total": [] if sem_marcador else [Elem(total, container=cab)],
                    "li.bl-rs__item": list(itens),
                    "nav.bl-rs__paginacao a[rel='next']": [Elem()] if proxima else []}
        self.erro_get, self.visitadas = erro_get, []

    def get(self, url):
        self.visitadas.append(url)
        if self.erro_get:
            raise self.erro_get

    def find_elements(self, by, sel):
        return self.dom.get(sel, [])


class TestBuscar:
    def test_url_de_busca(self):
        assert b.url_busca("dom casmurro") == "https://www.baixelivros.com.br/search?q=dom+casmurro"
        assert b.url_busca("dom casmurro", 2).endswith("&pg=2")

    def test_le_resultados_meta_e_paginacao(self):
        d = DriverBusca(itens=[item("Dom  Casmurro –\n Machado", "https://www.baixelivros.com.br/dom", "Machado · 2019")],
                        proxima=True)
        p = b.buscar(d, "dom casmurro")
        assert p.resultados == [("Dom Casmurro – Machado", "https://www.baixelivros.com.br/dom", "Machado · 2019")]
        assert p.tem_proxima and not p.sem_resultados and p.sugestao is None

    def test_sem_resultados_traz_sugestao_do_site(self):
        d = DriverBusca(total="Nenhum livro encontrado com esse termo", sugestao="Dom Casmurro",
                        itens=[item("Dom Casmurro", "https://www.baixelivros.com.br/dom")])
        p = b.buscar(d, "dom casmuro")
        assert p.sem_resultados and p.sugestao == "Dom Casmurro" and len(p.resultados) == 1

    def test_pagina_sem_o_formato_esperado_e_erro_do_site_nao_livro_nao_encontrado(self):
        with pytest.raises(b.ErroSite, match="formato esperado"):
            b.buscar(DriverBusca(sem_marcador=True), "qualquer")

    def test_timeout_vira_erro_do_site(self):
        with pytest.raises(b.ErroSite, match="demorou"):
            b.buscar(DriverBusca(erro_get=TimeoutException()), "qualquer")

    def test_ignora_itens_sem_titulo_ou_link(self):
        d = DriverBusca(itens=[item("", "https://www.baixelivros.com.br/x"), item("Sem link", ""), Elem()])
        assert b.buscar(d, "x").resultados == []


class Janelas:
    """`driver.switch_to`: só registra as trocas de aba."""

    def window(self, handle):
        pass


class DriverLivro:
    """Simula a página do livro e o Chrome baixando para a pasta definida via CDP."""

    def __init__(self, alvo="https://cdn.example.com/ugd/livro.pdf", botao=True, link_baixa=None, clique_baixa=None):
        self.alvo, self.tem_botao = alvo, botao
        self.link_baixa, self.clique_baixa = link_baixa, clique_baixa   # (nome, bytes) ou None
        self.destino, self.visitadas, self.cliques = None, [], 0
        self.handles = ["principal"]
        self.switch_to = Janelas()

    @property
    def window_handles(self):
        return list(self.handles)

    def get(self, url):
        self.visitadas.append(url)
        if url == self.alvo and self.link_baixa:
            self._gravar(*self.link_baixa)

    def _gravar(self, nome, dados):
        with open(os.path.join(self.destino, nome), "wb") as f:
            f.write(dados)

    def find_elements(self, by, sel):
        return [Elem(attrs={"data-target": self.alvo})] if self.tem_botao else []

    def execute_cdp_cmd(self, cmd, args):
        assert cmd == "Browser.setDownloadBehavior"
        self.destino = args["downloadPath"]

    def execute_script(self, js, el):
        self.cliques += 1
        if self.clique_baixa:
            self._gravar(*self.clique_baixa)
        self.handles.append("popup")  # um anúncio abre uma aba

    def close(self):
        self.handles.pop()


@pytest.fixture(autouse=True)
def tempos_curtos(monkeypatch):
    monkeypatch.setattr(b, "TIMEOUT_INICIO_LINK", 0.6)
    monkeypatch.setattr(b, "TIMEOUT_INICIO_BOTAO", 0.6)


class TestBaixar:
    URL = "https://www.baixelivros.com.br/livro"

    def test_prefere_o_link_direto_e_valida_o_arquivo(self, tmp_path):
        d = DriverLivro(link_baixa=("livro.pdf", PDF))
        caminho = b.baixar(d, self.URL, str(tmp_path), lambda: False)
        assert caminho.endswith("livro.pdf") and os.path.dirname(caminho).startswith(str(tmp_path))
        assert d.cliques == 0 and d.alvo in d.visitadas

    def test_cai_para_o_clique_se_o_link_nao_baixar(self, tmp_path):
        d = DriverLivro(clique_baixa=("livro.pdf", PDF))
        assert b.baixar(d, self.URL, str(tmp_path), lambda: False).endswith("livro.pdf")
        assert d.cliques == 1

    def test_nao_navega_para_link_que_nao_e_arquivo(self, tmp_path):
        d = DriverLivro(alvo="file:///C:/Users/x/secret.pdf", clique_baixa=("livro.pdf", PDF))
        b.baixar(d, self.URL, str(tmp_path), lambda: False)
        assert d.alvo not in d.visitadas

    def test_executavel_e_recusado_sem_tentar_de_novo(self, tmp_path):
        d = DriverLivro(link_baixa=("setup.exe", b"MZ"), clique_baixa=("livro.pdf", PDF))
        with pytest.raises(b.TipoNaoPermitido):
            b.baixar(d, self.URL, str(tmp_path), lambda: False)
        assert d.cliques == 0

    def test_pdf_falso_e_recusado(self, tmp_path):
        d = DriverLivro(link_baixa=("livro.pdf", b"<html>erro 403</html>"))
        with pytest.raises(b.FalhaDownload, match="PDF válido"):
            b.baixar(d, self.URL, str(tmp_path), lambda: False)

    def test_sem_botao(self, tmp_path):
        with pytest.raises(b.FalhaDownload, match="botão"):
            b.baixar(DriverLivro(botao=False), self.URL, str(tmp_path), lambda: False)

    def test_nada_baixa_informa_o_motivo(self, tmp_path):
        with pytest.raises(b.FalhaDownload, match="não começou"):
            b.baixar(DriverLivro(), self.URL, str(tmp_path), lambda: False)

    def test_fecha_abas_de_anuncio(self, tmp_path):
        d = DriverLivro(clique_baixa=("livro.pdf", PDF), alvo="")
        b.baixar(d, self.URL, str(tmp_path), lambda: False)
        assert d.handles == ["principal"]

    def test_cada_tentativa_usa_uma_subpasta_propria(self, tmp_path):
        d1, d2 = DriverLivro(link_baixa=("a.pdf", PDF)), DriverLivro(link_baixa=("a.pdf", PDF))
        c1 = b.baixar(d1, self.URL + "1", str(tmp_path), lambda: False)
        c2 = b.baixar(d2, self.URL + "2", str(tmp_path), lambda: False)
        assert os.path.dirname(c1) != os.path.dirname(c2)


class DriverProc:
    class Proc:
        pid = 4321

        def __init__(self, vivo):
            self.vivo = vivo

        def poll(self):
            return None if self.vivo else 0

    def __init__(self, vivo=True, quit_falha=False):
        self.service = type("S", (), {"process": self.Proc(vivo)})()
        self.quit_falha, self.saiu = quit_falha, False

    def quit(self):
        if self.quit_falha:
            raise WebDriverException("sessão morta")
        self.saiu = True


class TestEncerramento:
    def test_matar_driver_usa_taskkill_sem_shell(self, monkeypatch):
        chamadas = []

        def run(cmd, **kw):
            chamadas.append((cmd, kw))
            return type("R", (), {"returncode": 0})()
        monkeypatch.setattr(b.subprocess, "run", run)
        assert b.matar_driver(DriverProc()) is True
        cmd, kw = chamadas[0]
        assert cmd == ["taskkill", "/PID", "4321", "/T", "/F"] and not kw.get("shell") and kw["timeout"]

    def test_matar_driver_nao_toca_em_processo_ja_encerrado(self, monkeypatch):
        monkeypatch.setattr(b.subprocess, "run", lambda *a, **k: pytest.fail("não deveria chamar taskkill"))
        assert b.matar_driver(DriverProc(vivo=False)) is False
        assert b.matar_driver(object()) is False

    def test_encerrar_driver_quit_normal(self):
        d = DriverProc()
        b.encerrar_driver(d, lambda: False)
        assert d.saiu

    def test_encerrar_driver_cai_para_kill_se_quit_falhar(self, monkeypatch):
        mortos = []
        monkeypatch.setattr(b, "matar_driver", mortos.append)
        d = DriverProc(quit_falha=True)
        b.encerrar_driver(d, lambda: False)
        assert mortos == [d]

    def test_encerrar_driver_cancelado_mata_e_nunca_levanta(self, monkeypatch):
        mortos = []

        def matar(d):
            mortos.append(d)
            raise RuntimeError("x")
        monkeypatch.setattr(b, "matar_driver", matar)
        d = DriverProc()
        b.encerrar_driver(d, lambda: True)  # não deve levantar
        assert mortos == [d] and not d.saiu
        b.encerrar_driver(None, lambda: False)


# --------------------------------------------------------------------------- #
# fluxo completo (executar) com buscar/baixar/criar_driver falsos
# --------------------------------------------------------------------------- #
class Fluxo:
    """Monta um cenário: `paginas` é {(consulta, pagina): Pagina}; `downloads` é {url: Exceção}."""

    def __init__(self, monkeypatch, tmp_path, paginas, downloads=None):
        self.pasta = str(tmp_path / "Teste de Download")
        self.paginas, self.downloads = paginas, downloads or {}
        self.buscas, self.abertos, self.logs, self.driver = [], [], [], DriverProc()
        self.encerrados = []
        monkeypatch.setattr(b, "criar_driver", lambda pasta: self.driver)
        monkeypatch.setattr(b, "buscar", self.buscar)
        monkeypatch.setattr(b, "baixar", self.baixar)
        monkeypatch.setattr(b, "encerrar_driver", lambda d, c: self.encerrados.append((d, c())))

    def buscar(self, driver, consulta, pagina):
        self.buscas.append((consulta, pagina))
        return self.paginas.get((consulta, pagina), b.Pagina([], True, None, False))

    def baixar(self, driver, url, trabalho, cancelado):
        self.abertos.append(url)
        r = self.downloads.get(url)
        if isinstance(r, Exception):
            raise r
        caminho = os.path.join(trabalho, "dl.pdf")
        with open(caminho, "wb") as f:
            f.write(PDF)
        return caminho

    def rodar(self, titulo, cancelado=lambda: False):
        return b.executar(titulo, self.pasta, self.logs.append, cancelado, lambda d: None)

    def temporarios(self):
        return [n for n in os.listdir(self.pasta) if n.startswith(b.PREFIXO_TMP)]


DOM = "https://www.baixelivros.com.br/dom"
OUTRO = "https://www.baixelivros.com.br/outro"


def pag(*itens, sem=False, sugestao=None, proxima=False):
    return b.Pagina([(n, u, "") for n, u in itens], sem, sugestao, proxima)


class TestExecutar:
    def test_baixa_o_melhor_candidato_e_renomeia(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("dom casmurro", 1): pag(("Dom Casmurro – Machado de Assis", DOM))})
        r = f.rodar("dom casmurro")
        assert r == b.Resultado("Dom Casmurro – Machado de Assis", "Dom Casmurro – Machado de Assis.pdf", False)
        assert os.path.isfile(os.path.join(f.pasta, r.arquivo))
        assert f.temporarios() == []                     # pasta temporária removida
        assert f.encerrados == [(f.driver, False)]

    def test_livro_nao_encontrado_quando_nada_e_compativel(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("xyzabc", 1): pag(("Dom Casmurro", DOM), sem=True)})
        with pytest.raises(b.LivroNaoEncontrado):
            f.rodar("xyzabc")
        assert f.abertos == [] and f.temporarios() == []

    def test_livro_achado_mas_sem_download_nao_vira_nao_encontrado(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("dom casmurro", 1): pag(("Dom Casmurro", DOM))},
                  {DOM: b.FalhaDownload("a página do livro não tem botão de download")})
        with pytest.raises(b.DownloadFalhou) as e:
            f.rodar("dom casmurro")
        assert e.value.nome == "Dom Casmurro" and "botão" in e.value.motivo

    def test_tenta_o_proximo_candidato_quando_o_primeiro_falha(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path,
                  {("dom casmurro", 1): pag(("Dom Casmurro", DOM), ("Dom Casmurro – Machado de Assis", OUTRO))},
                  {DOM: b.FalhaDownload("sem botão")})
        assert f.rodar("dom casmurro").nome == "Dom Casmurro – Machado de Assis"
        assert set(f.abertos) == {DOM, OUTRO}

    def test_pagina_2_so_quando_ha_proxima_e_nada_baixou(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("dom casmurro", 1): pag(("Outro", OUTRO), proxima=True),
                                          ("dom casmurro", 2): pag(("Dom Casmurro", DOM))})
        assert f.rodar("dom casmurro").nome == "Dom Casmurro"
        assert ("dom casmurro", 2) in f.buscas

    def test_nao_pede_pagina_2_quando_a_1_resolve(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("dom casmurro", 1): pag(("Dom Casmurro", DOM), proxima=True)})
        f.rodar("dom casmurro")
        assert f.buscas == [("dom casmurro", 1)]

    def test_limite_de_paginas(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("xyzabc", p): pag(("Nada", OUTRO), proxima=True) for p in (1, 2, 3, 4)})
        with pytest.raises(b.LivroNaoEncontrado):
            f.rodar("xyzabc")
        assert max(p for (c, p) in f.buscas if c == "xyzabc") == b.MAX_PAGINAS

    def test_usa_a_sugestao_do_site_antes_das_variantes(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("cazmurro", 1): pag(sem=True, sugestao="Casmurro"),
                                          ("Casmurro", 1): pag(("Dom Casmurro", DOM))})
        assert f.rodar("cazmurro").nome == "Dom Casmurro"
        assert f.buscas[:2] == [("cazmurro", 1), ("Casmurro", 1)]

    def test_livro_que_ja_existe_nao_e_baixado_de_novo(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("dom casmurro", 1): pag(("Dom Casmurro", DOM))})
        os.makedirs(f.pasta)
        with open(os.path.join(f.pasta, "Dom Casmurro.pdf"), "wb") as arq:
            arq.write(PDF)
        assert f.rodar("dom casmurro") == b.Resultado("Dom Casmurro", "Dom Casmurro.pdf", True)
        assert f.abertos == []

    def test_erro_do_site_propaga_como_erro_e_nao_como_nao_encontrado(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {})
        monkeypatch.setattr(b, "buscar", lambda *a: (_ for _ in ()).throw(b.ErroSite("mudou")))
        with pytest.raises(b.ErroSite):
            f.rodar("dom casmurro")
        assert f.temporarios() == []

    def test_cancelar_limpa_tudo(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {("dom casmurro", 1): pag(("Dom Casmurro", DOM))})
        with pytest.raises(b.Cancelado):
            f.rodar("dom casmurro", cancelado=lambda: True)
        assert f.encerrados == [(f.driver, True)] and f.temporarios() == []

    def test_excecao_apos_cancelar_vira_cancelado(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {})
        estado = {"cancelado": False}

        def buscar(*a):
            estado["cancelado"] = True
            raise WebDriverException("chrome morreu")
        monkeypatch.setattr(b, "buscar", buscar)
        with pytest.raises(b.Cancelado):
            f.rodar("dom casmurro", cancelado=lambda: estado["cancelado"])

    def test_falha_ao_abrir_o_chrome_e_erro_e_limpa_a_pasta_temporaria(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path, {})
        monkeypatch.setattr(b, "criar_driver", lambda p: (_ for _ in ()).throw(WebDriverException("sem chrome")))
        with pytest.raises(WebDriverException):
            f.rodar("dom casmurro")
        assert f.temporarios() == []

    def test_erro_do_navegador_num_candidato_nao_aborta_os_outros(self, monkeypatch, tmp_path):
        f = Fluxo(monkeypatch, tmp_path,
                  {("dom casmurro", 1): pag(("Dom Casmurro", DOM), ("Dom Casmurro – Machado de Assis", OUTRO))},
                  {DOM: WebDriverException("boom")})
        assert f.rodar("dom casmurro").nome == "Dom Casmurro – Machado de Assis"


# --------------------------------------------------------------------------- #
# interface
# --------------------------------------------------------------------------- #
@pytest.fixture
def app(monkeypatch, tmp_path):
    import tkinter
    a, erro = None, None
    for _ in range(5):  # o Tcl do Windows falha de vez em quando ao criar muitas janelas em sequência
        try:
            a = b.App()
            break
        except tkinter.TclError as e:
            erro = e
            time.sleep(0.5)
    if a is None:
        pytest.skip(f"sem ambiente gráfico: {erro}")
    a.pasta = str(tmp_path)
    popups = []
    monkeypatch.setattr(b.messagebox, "showerror", lambda *x, **k: popups.append(("erro", x)))
    monkeypatch.setattr(b.messagebox, "showinfo", lambda *x, **k: popups.append(("info", x)))
    monkeypatch.setattr(b.messagebox, "showwarning", lambda *x, **k: popups.append(("aviso", x)))
    monkeypatch.setattr(a, "avisar", lambda ok: None)
    a.popups = popups
    yield a
    a.destroy()


def tags_do_log(app, tag):
    r = app.log.tag_ranges(tag)
    return [app.log.get(str(i), str(j)).strip() for i, j in zip(r[::2], r[1::2])]


class TestInterface:
    def test_concluido_so_no_log_em_verde(self, app):
        app.finalizar("ok", b.Resultado("Dom Casmurro", "Dom Casmurro.pdf", False))
        assert any("Concluído" in t for t in tags_do_log(app, "ok"))
        assert app.popups == [] and app.msg.cget("text") == app.PROMPT

    def test_nao_encontrado_so_no_log_em_vermelho(self, app):
        app.finalizar("nao_encontrado", "zzz")
        assert any("Livro não encontrado" in t and "zzz" in t for t in tags_do_log(app, "erro"))
        assert app.popups == []

    def test_download_falhou_em_vermelho_com_o_motivo(self, app):
        app.finalizar("download_falhou", ("Dom Casmurro", "a página do livro não tem botão de download"))
        assert any("Dom Casmurro" in t and "botão" in t for t in tags_do_log(app, "erro")) and app.popups == []

    def test_erro_real_mostra_traceback_no_log_e_popup(self, app):
        app.finalizar("erro", ("ErroSite: mudou", "Traceback (most recent call last):\n  ...", "dica"))
        assert any("ErroSite: mudou" in t for t in tags_do_log(app, "erro"))
        assert "Traceback" in app.log.get("1.0", "end") and app.popups[0][0] == "erro"

    def test_cancelado_nao_e_verde_nem_vermelho(self, app):
        app.finalizar("cancelado", "x")
        assert "Busca interrompida" in app.log.get("1.0", "end")
        assert tags_do_log(app, "ok") == [] == tags_do_log(app, "erro")

    def test_finalizar_reabilita_controles_e_limpa_o_driver(self, app):
        app.botao.state(["disabled"])
        app.driver, app.thread = object(), object()
        app.finalizar("nao_encontrado", "x")
        assert not app.botao.instate(["disabled"]) and app.driver is None and app.thread is None
        assert app.botao_parar.instate(["disabled"])

    def test_botao_abrir_livro_so_para_tipos_seguros(self, app):
        app.finalizar("ok", b.Resultado("A", "A.zip", False))
        app.update()
        assert not app.botao_arquivo.winfo_ismapped()
        app.finalizar("ok", b.Resultado("A", "A.pdf", False))
        app.update()
        assert app.botao_arquivo.winfo_ismapped()

    def test_abrir_livro_nao_executa_tipos_perigosos(self, app, monkeypatch, tmp_path):
        aberto = []
        monkeypatch.setattr(b.os, "startfile", aberto.append, raising=False)
        (tmp_path / "x.exe").write_bytes(b"MZ")
        (tmp_path / "ok.pdf").write_bytes(PDF)
        app.ultimo_arquivo = str(tmp_path / "x.exe")
        app.abrir_arquivo()
        assert aberto == []
        app.ultimo_arquivo = str(tmp_path / "ok.pdf")
        app.abrir_arquivo()
        assert aberto == [str(tmp_path / "ok.pdf")]

    def test_fila_continua_sendo_lida_mesmo_se_finalizar_falhar(self, app, monkeypatch):
        agendados = []
        monkeypatch.setattr(app, "after", lambda ms, fn=None, *a: agendados.append((ms, fn)))
        monkeypatch.setattr(app, "finalizar", lambda *a: (_ for _ in ()).throw(RuntimeError("tcl quebrou")))
        app.fila.put(("ok", b.Resultado("A", "A.pdf", False)))
        app.fila.put(("status", "mensagem seguinte"))
        app.processar_fila()
        assert agendados and agendados[-1][1] == app.processar_fila          # continua agendada
        log = app.log.get("1.0", "end")
        assert "ERRO INTERNO" in log and "mensagem seguinte" in log          # e a mensagem seguinte foi processada

    def test_trabalhar_traduz_excecoes_para_a_fila(self, app, monkeypatch):
        def rodar(exc):
            monkeypatch.setattr(b, "executar", lambda *a, **k: (_ for _ in ()).throw(exc))
            app.trabalhar("x", threading.Event())
            return app.fila.get_nowait()
        assert rodar(b.LivroNaoEncontrado("x"))[0] == "nao_encontrado"
        assert rodar(b.Cancelado())[0] == "cancelado"
        assert rodar(b.DownloadFalhou("L", "m")) == ("download_falhou", ("L", "m"))
        tipo, (resumo, detalhe, dica) = rodar(b.ErroSite("mudou"))
        assert tipo == "erro" and resumo == "ErroSite: mudou" and "Traceback" in detalhe and "site" in dica
        tipo, (resumo, _d, dica) = rodar(WebDriverException("\nMessage: falhou\n"))
        assert "WebDriverException" in resumo and "Chrome" in dica

    def test_parar_com_driver_encerra_o_navegador_na_hora(self, app, monkeypatch):
        mortos = []
        monkeypatch.setattr(b, "matar_driver", mortos.append)
        app.driver = "drv"
        app.botao_parar.state(["!disabled"])
        app.parar()
        for _ in range(50):                  # o kill roda numa thread, para não travar a janela
            if mortos:
                break
            time.sleep(0.05)
        assert mortos == ["drv"] and app.parar_evento.is_set() and app.botao_parar.instate(["disabled"])
        app.parar()                          # segundo clique não repete o kill
        time.sleep(0.2)
        assert mortos == ["drv"]

    def test_parar_sem_driver_so_sinaliza(self, app, monkeypatch):
        mortos = []
        monkeypatch.setattr(b, "matar_driver", mortos.append)
        app.driver = None
        app.parar()
        assert app.parar_evento.is_set() and mortos == []

    def test_fechar_janela_espera_o_worker_e_mata_o_driver(self, app, monkeypatch):
        mortos, ordem = [], []
        monkeypatch.setattr(b, "matar_driver", mortos.append)
        monkeypatch.setattr(app, "destroy", lambda: ordem.append("destroy"))
        worker = threading.Thread(target=lambda: (time.sleep(0.3), ordem.append("worker terminou")))
        worker.start()
        app.thread, app.driver = worker, "drv"
        app.fechar_janela()
        assert mortos == ["drv"] and ordem == ["worker terminou", "destroy"]
        agendados = []                       # depois de fechar, a fila não pode se reagendar
        monkeypatch.setattr(app, "after", lambda *a: agendados.append(a))
        app.processar_fila()
        assert agendados == []
        monkeypatch.undo()                   # devolve o destroy() real para o fixture encerrar a janela


# --------------------------------------------------------------------------- #
# integração com o site real (pytest -m integration)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
class TestSiteReal:
    def rodar(self, titulo, tmp_path, cancelado=lambda: False):
        logs = []
        pasta = str(tmp_path / "Teste de Download")
        return pasta, logs, b.executar(titulo, pasta, logs.append, cancelado, lambda d: None)

    @pytest.mark.parametrize("digitado", ["dom casmurro", "dom casmuro", "cazmurro"])
    def test_baixa_dom_casmurro_mesmo_com_erro_de_digitacao(self, tmp_path, digitado):
        pasta, logs, r = self.rodar(digitado, tmp_path)
        caminho = os.path.join(pasta, r.arquivo)
        assert r.nome.startswith("Dom Casmurro") and r.arquivo.endswith(".pdf")
        with open(caminho, "rb") as f:
            assert f.read(5) == b"%PDF-"
        assert os.path.getsize(caminho) > 10_000
        assert [n for n in os.listdir(pasta) if n.startswith(b.PREFIXO_TMP)] == []

    def test_livro_inexistente(self, tmp_path):
        with pytest.raises(b.LivroNaoEncontrado):
            self.rodar("zzqxqw kkjjhh", tmp_path)
        assert os.listdir(tmp_path / "Teste de Download") == []

    def test_a_pagina_de_busca_ainda_tem_o_formato_esperado(self, tmp_path):
        d = b.criar_driver(str(tmp_path))
        try:
            p = b.buscar(d, "dom casmurro")
            assert p.resultados or p.sem_resultados
            p2 = b.buscar(d, "livro")
            assert len(p2.resultados) >= 10 and p2.tem_proxima and not p2.sem_resultados
        finally:
            d.quit()
