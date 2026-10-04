"""Testes da fila de downloads em lote."""
import threading
import time

import fila as f
from erros import Cancelado, ErroRede
from helpers import PDF, FakeFonte, FakeHttp, arq, contexto, obra


def montar(tmp_path, titulos_obras, trabalhadores=3, http=None):
    """`titulos_obras`: {"titulo digitado": Obra | None}. Cada obra vira um download distinto."""
    obras, arquivos, conteudos = {}, {}, {}
    for digitado, o in titulos_obras.items():
        if o is None:
            continue
        obras.setdefault(digitado, []).append(o)
        url = f"https://x.test/{o.id}.pdf"
        arquivos[o.id], conteudos[url] = [arq(url)], PDF + str(o.id).encode()
    fonte = FakeFonte(obras=obras, arquivos=arquivos)
    http = http or FakeHttp(conteudos)
    http.conteudos = {**conteudos, **http.conteudos}
    ctx = contexto(tmp_path, [fonte], http)
    eventos, logs = [], []
    q = f.FilaDownloads(ctx, ao_mudar=lambda i: eventos.append((i.id, i.estado)),
                        ao_log=lambda i, t, tag: logs.append((i.id, t, tag)), trabalhadores=trabalhadores)
    return q, ctx, http, eventos, logs


def O(i, titulo):
    return obra(id=str(i), titulo=titulo, autor="")


def test_processa_varios_titulos_e_informa_estados(tmp_path):
    q, ctx, _, eventos, logs = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro"), "iracema": O(2, "Iracema")})
    itens = q.adicionar(["dom casmurro", "iracema"])
    assert q.aguardar(10)
    assert [i.estado for i in itens] == [f.CONCLUIDO, f.CONCLUIDO]
    assert [i.livro for i in itens] == ["Dom Casmurro", "Iracema"] and all(i.arquivo.endswith(".pdf") for i in itens)
    estados_1 = [e for iid, e in eventos if iid == 1]
    assert estados_1[0] == f.NA_FILA and f.BUSCANDO in estados_1 and f.BAIXANDO in estados_1 and estados_1[-1] == f.CONCLUIDO
    assert len(ctx.biblioteca.listar()) == 2


def test_resultado_final_so_no_log_verde_e_vermelho(tmp_path):
    q, _, _, _, logs = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro"), "xyzabc": None})
    q.adicionar(["dom casmurro", "xyzabc"])
    assert q.aguardar(10)
    ok = [t for i, t, tag in logs if tag == "ok"]
    erro = [t for i, t, tag in logs if tag == "erro"]
    assert len(ok) == 1 and ok[0].startswith("✔ Concluído: “Dom Casmurro”")
    assert erro == ["✖ Livro não encontrado: “xyzabc”"]


def test_downloads_acontecem_em_paralelo(tmp_path):
    http = FakeHttp()
    barreira = threading.Barrier(3, timeout=5)      # os 3 downloads precisam estar ativos ao mesmo tempo
    http.antes = lambda url, c: barreira.wait()
    q, *_ = montar(tmp_path, {f"livro {i}": O(i, f"Livro {i}") for i in (1, 2, 3)}, trabalhadores=3, http=http)
    itens = q.adicionar(["livro 1", "livro 2", "livro 3"])
    assert q.aguardar(15) and [i.estado for i in itens] == [f.CONCLUIDO] * 3


def test_um_trabalhador_processa_em_serie(tmp_path):
    http = FakeHttp()
    ativos, pico, trava = [0], [0], threading.Lock()

    def antes(url, c):
        with trava:
            ativos[0] += 1
            pico[0] = max(pico[0], ativos[0])
        time.sleep(0.05)
        with trava:
            ativos[0] -= 1
    http.antes = antes
    q, *_ = montar(tmp_path, {f"livro {i}": O(i, f"Livro {i}") for i in (1, 2, 3)}, trabalhadores=1, http=http)
    q.adicionar(["livro 1", "livro 2", "livro 3"])
    assert q.aguardar(10) and pico[0] == 1


def test_titulos_vazios_e_espacos_sao_ignorados(tmp_path):
    q, *_ = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro")})
    itens = q.adicionar(["", "   ", "  dom   casmurro \n"])
    assert [i.titulo for i in itens] == ["dom casmurro"] and q.aguardar(10)


def test_progresso_do_download(tmp_path):
    q, *_ = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro")})
    vistos = []
    q.ao_mudar = lambda i: vistos.append((i.estado, i.baixado, i.total))
    q.adicionar(["dom casmurro"])
    assert q.aguardar(10)
    assert (f.BAIXANDO, len(PDF) + 1, len(PDF) + 1) in vistos
    assert q.itens[0].percentual == 100


def test_parar_tudo_interrompe_download_em_andamento_e_itens_pendentes(tmp_path):
    http = FakeHttp()
    iniciou = threading.Event()

    def antes(url, cancelado):
        iniciou.set()
        while not cancelado():
            time.sleep(0.02)
        raise Cancelado()
    http.antes = antes
    q, *_ = montar(tmp_path, {f"livro {i}": O(i, f"Livro {i}") for i in (1, 2, 3)}, trabalhadores=1, http=http)
    itens = q.adicionar(["livro 1", "livro 2", "livro 3"])
    assert iniciou.wait(5)
    q.parar_tudo()
    assert q.aguardar(10)
    assert [i.estado for i in itens] == [f.PARADO] * 3 and q.ativos() == 0


def test_parar_um_item_nao_afeta_os_outros(tmp_path):
    http = FakeHttp()
    segurar = threading.Event()

    def antes(url, cancelado):
        if url.endswith("/1.pdf"):
            while not cancelado():
                time.sleep(0.02)
            raise Cancelado()
        segurar.wait(5)
    http.antes = antes
    q, *_ = montar(tmp_path, {"livro 1": O(1, "Livro 1"), "livro 2": O(2, "Livro 2")}, http=http)
    a, b = q.adicionar(["livro 1", "livro 2"])
    time.sleep(0.2)
    q.parar(a)
    segurar.set()
    assert q.aguardar(10) and (a.estado, b.estado) == (f.PARADO, f.CONCLUIDO)


def test_repetir_falhas(tmp_path):
    q, ctx, http, _, logs = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro"), "iracema": O(2, "Iracema")})
    http.conteudos["https://x.test/2.pdf"] = ErroRede("HTTP 500", status=500)
    itens = q.adicionar(["dom casmurro", "iracema", "livro que nao existe"])
    assert q.aguardar(10)
    assert [i.estado for i in itens] == [f.CONCLUIDO, f.FALHOU, f.NAO_ENCONTRADO]
    http.conteudos["https://x.test/2.pdf"] = PDF + b"2"        # o problema foi resolvido
    refeitos = q.repetir_falhas()
    assert {i.titulo for i in refeitos} == {"iracema", "livro que nao existe"} and q.aguardar(10)
    assert [i.estado for i in itens] == [f.CONCLUIDO, f.CONCLUIDO, f.NAO_ENCONTRADO]
    assert itens[1].baixado > 0 and q.repetir_falhas()[0].titulo == "livro que nao existe"
    assert q.aguardar(10)


def test_falha_de_download_aparece_em_vermelho_com_o_motivo(tmp_path):
    q, _, http, _, logs = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro")})
    http.conteudos["https://x.test/1.pdf"] = b"<html>erro</html>"
    item, = q.adicionar(["dom casmurro"])
    assert q.aguardar(10) and item.estado == f.FALHOU
    assert any(tag == "erro" and "Dom Casmurro" in t and "PDF válido" in t for _, t, tag in logs)


def test_fontes_fora_do_ar_marca_falhou(tmp_path):
    ctx = contexto(tmp_path, [FakeFonte(erro=ErroRede("sem conexão"))])
    logs = []
    q = f.FilaDownloads(ctx, ao_log=lambda i, t, tag: logs.append((t, tag)))
    item, = q.adicionar(["dom casmurro"])
    assert q.aguardar(10) and item.estado == f.FALHOU
    assert any(tag == "erro" and "não responderam" in t for t, tag in logs)


def test_livro_ja_na_biblioteca(tmp_path):
    q, *_ = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro")})
    q.adicionar(["dom casmurro"])
    assert q.aguardar(10)
    segundo, = q.adicionar(["dom casmurro"])
    assert q.aguardar(10) and segundo.estado == f.EXISTIA


def test_excecao_inesperada_nao_derruba_a_thread_de_trabalho(tmp_path, monkeypatch):
    q, _, _, _, logs = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro")}, trabalhadores=1)
    real = f.processar
    chamadas = []

    def processar(titulo, ctx, cancelado):
        chamadas.append(titulo)
        if len(chamadas) == 1:
            raise RuntimeError("bug inesperado")
        return real(titulo, ctx, cancelado)
    monkeypatch.setattr(f, "processar", processar)
    a, b = q.adicionar(["dom casmurro", "dom casmurro"])
    assert q.aguardar(10) and (a.estado, b.estado) == (f.FALHOU, f.CONCLUIDO)
    assert any("Erro inesperado: RuntimeError" in t for _, t, _ in logs)


def test_quem_escuta_pode_falhar_sem_afetar_o_download(tmp_path):
    """Regressão: um erro no log/na janela matava a thread de trabalho e deixava o item preso."""
    q, ctx, *_ = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro"), "iracema": O(2, "Iracema")}, trabalhadores=1)

    def quebra(*args):
        raise UnicodeEncodeError("charmap", "✔", 0, 1, "não imprime")
    q.ao_log = quebra
    q.ao_mudar = quebra
    itens = q.adicionar(["dom casmurro", "iracema"])
    assert q.aguardar(10)
    assert [i.estado for i in itens] == [f.CONCLUIDO, f.CONCLUIDO]
    assert len(ctx.biblioteca.listar()) == 2


def test_contexto_base_nao_e_alterado_pelos_itens(tmp_path):
    q, ctx, *_ = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro")})
    log_original, estado_original = ctx.log, ctx.estado
    q.adicionar(["dom casmurro"])
    assert q.aguardar(10) and ctx.log is log_original and ctx.estado is estado_original


def test_aguardar_com_timeout(tmp_path):
    http = FakeHttp()
    liberar = threading.Event()
    http.antes = lambda url, c: liberar.wait(5)
    q, *_ = montar(tmp_path, {"dom casmurro": O(1, "Dom Casmurro")}, http=http)
    q.adicionar(["dom casmurro"])
    assert q.aguardar(0.2) is False
    liberar.set()
    assert q.aguardar(10) is True


def test_item_percentual_sem_total():
    assert f.Item(1, "x").percentual is None
    assert f.Item(1, "x", baixado=50, total=200).percentual == 25
