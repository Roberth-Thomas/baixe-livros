"""Testes do cliente HTTP: domínios permitidos, JSON, tentativas e download com retomada (servidor local real)."""
import json
import os

import pytest

import rede
from erros import Cancelado, ErroRede
from helpers import Servidor

CORPO = bytes(range(256)) * 200   # 51.200 bytes


@pytest.fixture
def srv():
    s = Servidor()
    yield s
    s.parar()


@pytest.fixture
def http(srv):
    return rede.Http(hosts=("127.0.0.1",), esquemas=("http",), timeout=5, tentativas=3, espera=0)


class TestHostPermitido:
    @pytest.mark.parametrize("url", [
        "https://archive.org/x", "https://ia800.us.archive.org/x", "https://www.gutenberg.org/ebooks/1",
        "https://gutendex.com/books", "HTTPS://ARCHIVE.ORG/x",
    ])
    def test_permitidos(self, url):
        assert rede.host_permitido(url, rede.HOSTS_PADRAO)

    @pytest.mark.parametrize("url", [
        "", None, "http://archive.org/x", "https://archive.org.evil.com/x", "https://evilarchive.org/x",
        "https://evil.com/archive.org", "file:///C:/x.pdf", "javascript:alert(1)", "//archive.org/x",
        "https://user@evil.com@archive.org.evil.com/",
    ])
    def test_negados(self, url):
        assert not rede.host_permitido(url, rede.HOSTS_PADRAO)


class TestGetJson:
    def test_le_json(self, srv, http):
        srv.rotas["/a"] = {"corpo": json.dumps({"x": [1, 2]}).encode()}
        assert http.get_json(srv.url("/a")) == {"x": [1, 2]}

    def test_cabecalhos_identificam_o_app(self, srv, http):
        srv.rotas["/a"] = {"corpo": b"{}"}
        http.get_json(srv.url("/a"))
        cab = {k.lower(): v for k, v in srv.requisicoes[0][1].items()}
        assert cab["user-agent"].startswith("BaixeLivros/") and cab["accept-encoding"] == "identity"

    def test_resposta_que_nao_e_json(self, srv, http):
        srv.rotas["/a"] = {"corpo": b"<html>oops</html>"}
        with pytest.raises(ErroRede, match="não é JSON"):
            http.get_json(srv.url("/a"))

    def test_resposta_grande_demais(self, srv, http, monkeypatch):
        monkeypatch.setattr(rede, "MAX_JSON", 100)
        srv.rotas["/a"] = {"corpo": b'{"x": "' + b"a" * 500 + b'"}'}
        with pytest.raises(ErroRede, match="grande demais"):
            http.get_json(srv.url("/a"))

    def test_404_nao_tenta_de_novo(self, srv, http):
        with pytest.raises(ErroRede) as e:
            http.get_json(srv.url("/nao-existe"))
        assert e.value.status == 404 and not e.value.transitorio and len(srv.requisicoes) == 1

    def test_503_tenta_de_novo_e_consegue(self, srv, http):
        srv.rotas["/a"] = {"corpo": b'{"ok": true}', "falhar_primeiras": 2}
        assert http.get_json(srv.url("/a")) == {"ok": True}
        assert len(srv.requisicoes) == 3

    def test_503_persistente_esgota_as_tentativas(self, srv, http):
        srv.rotas["/a"] = {"corpo": b"{}", "falhar_primeiras": 99}
        with pytest.raises(ErroRede) as e:
            http.get_json(srv.url("/a"))
        assert e.value.status == 503 and len(srv.requisicoes) == 3

    def test_parametro_tentativas_limita(self, srv, http):
        srv.rotas["/a"] = {"corpo": b"{}", "falhar_primeiras": 99}
        with pytest.raises(ErroRede):
            http.get_json(srv.url("/a"), tentativas=1)
        assert len(srv.requisicoes) == 1

    def test_servidor_fora_do_ar(self, http):
        porta_morta = Servidor()
        url = porta_morta.url("/a")
        porta_morta.parar()
        with pytest.raises(ErroRede) as e:
            http.get_json(url, tentativas=1)
        assert e.value.transitorio

    def test_dominio_nao_permitido_nem_chega_a_conectar(self, srv, http):
        with pytest.raises(ErroRede, match="não permitido"):
            http.get_json("http://localhost:1/x")
        assert srv.requisicoes == []

    def test_cancelamento_durante_a_espera_entre_tentativas(self, srv):
        srv.rotas["/a"] = {"corpo": b"{}", "falhar_primeiras": 99}
        h = rede.Http(hosts=("127.0.0.1",), esquemas=("http",), timeout=5, tentativas=3, espera=30)
        marcas = iter([False, False, True, True, True, True])
        with pytest.raises(Cancelado):
            h.get_json(srv.url("/a"), cancelado=lambda: next(marcas, True))


class TestRedirecionamento:
    def test_segue_redirecionamento_dentro_do_dominio(self, srv, http):
        srv.rotas["/a"] = {"redirect": srv.url("/b")}
        srv.rotas["/b"] = {"corpo": b'{"destino": 1}'}
        assert http.get_json(srv.url("/a")) == {"destino": 1}

    def test_recusa_redirecionamento_para_fora(self, srv, http):
        srv.rotas["/a"] = {"redirect": f"http://localhost:{srv.porta}/b"}   # "localhost" não está na lista
        srv.rotas["/b"] = {"corpo": b"{}"}
        with pytest.raises(ErroRede, match="redirecionamento"):
            http.get_json(srv.url("/a"))
        assert [p for p, _ in srv.requisicoes] == ["/a"]

    def test_recusa_redirecionamento_para_esquema_perigoso(self, srv, http):
        srv.rotas["/a"] = {"redirect": "file:///C:/Windows/win.ini"}
        with pytest.raises(ErroRede):
            http.baixar(srv.url("/a"), "ignorado.part")


class TestBaixar:
    def test_baixa_o_arquivo_inteiro_com_progresso(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO}
        parcial, marcas = str(tmp_path / "l.part"), []
        n = http.baixar(srv.url("/l.pdf"), parcial, lambda b, t: marcas.append((b, t)))
        assert n == len(CORPO) and open(parcial, "rb").read() == CORPO
        assert marcas[-1] == (len(CORPO), len(CORPO)) and [b for b, _ in marcas] == sorted(b for b, _ in marcas)
        assert not os.path.exists(parcial + ".json")      # metadados de retomada removidos ao concluir

    def test_retoma_de_onde_parou(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO, "etag": '"v1"'}
        parcial = str(tmp_path / "l.part")
        open(parcial, "wb").write(CORPO[:20000])
        json.dump({"url": srv.url("/l.pdf"), "validador": '"v1"'}, open(parcial + ".json", "w"))
        http.baixar(srv.url("/l.pdf"), parcial)
        assert open(parcial, "rb").read() == CORPO
        cab = {k.lower(): v for k, v in srv.requisicoes[-1][1].items()}
        assert cab["range"] == "bytes=20000-" and cab["if-range"] == '"v1"'

    def test_arquivo_mudou_no_servidor_recomeca_do_zero(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO, "etag": '"v2"'}
        parcial = str(tmp_path / "l.part")
        open(parcial, "wb").write(b"lixo-de-uma-versao-antiga" * 100)
        json.dump({"url": srv.url("/l.pdf"), "validador": '"v1"'}, open(parcial + ".json", "w"))
        http.baixar(srv.url("/l.pdf"), parcial)
        assert open(parcial, "rb").read() == CORPO

    def test_servidor_sem_suporte_a_range_recomeca(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO, "range": False}
        parcial = str(tmp_path / "l.part")
        open(parcial, "wb").write(CORPO[:1000])
        json.dump({"url": srv.url("/l.pdf"), "validador": '"v1"'}, open(parcial + ".json", "w"))
        http.baixar(srv.url("/l.pdf"), parcial)
        assert open(parcial, "rb").read() == CORPO

    def test_parcial_de_outra_url_nao_e_reaproveitado(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO}
        parcial = str(tmp_path / "l.part")
        open(parcial, "wb").write(b"dados-de-outro-livro" * 50)
        json.dump({"url": "http://outra/url", "validador": '"v1"'}, open(parcial + ".json", "w"))
        http.baixar(srv.url("/l.pdf"), parcial)
        assert open(parcial, "rb").read() == CORPO
        assert "range" not in {k.lower() for k in srv.requisicoes[-1][1]}

    def test_parcial_maior_que_o_arquivo_416_recomeca(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO[:3000]}
        parcial = str(tmp_path / "l.part")
        open(parcial, "wb").write(b"x" * 5000)
        json.dump({"url": srv.url("/l.pdf"), "validador": '"v1"'}, open(parcial + ".json", "w"))
        http.baixar(srv.url("/l.pdf"), parcial)
        assert open(parcial, "rb").read() == CORPO[:3000]

    def test_conexao_cortada_no_meio_e_retomada_automaticamente(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO, "cortar_em": 20000, "cortar_vezes": 1}
        parcial = str(tmp_path / "l.part")
        http.baixar(srv.url("/l.pdf"), parcial)
        assert open(parcial, "rb").read() == CORPO
        assert len(srv.requisicoes) == 2
        assert {k.lower(): v for k, v in srv.requisicoes[1][1].items()}["range"] == "bytes=20000-"

    def test_cancelar_mantem_o_parcial_e_depois_retoma(self, srv, http, tmp_path):
        srv.rotas["/l.pdf"] = {"corpo": CORPO * 10, "pausa": 0.01}
        parcial, estado = str(tmp_path / "l.part"), {"baixado": 0}

        def progresso(b, t):
            estado["baixado"] = b
        with pytest.raises(Cancelado):
            http.baixar(srv.url("/l.pdf"), parcial, progresso, cancelado=lambda: estado["baixado"] > 40000)
        assert 0 < os.path.getsize(parcial) < len(CORPO) * 10 and os.path.exists(parcial + ".json")
        srv.rotas["/l.pdf"]["pausa"] = 0
        http.baixar(srv.url("/l.pdf"), parcial)
        assert open(parcial, "rb").read() == CORPO * 10

    def test_arquivo_grande_demais_e_recusado_sem_baixar(self, srv, http, tmp_path, monkeypatch):
        monkeypatch.setattr(rede, "MAX_ARQUIVO", 1000)
        srv.rotas["/l.pdf"] = {"corpo": CORPO}
        parcial = str(tmp_path / "l.part")
        with pytest.raises(ErroRede, match="grande demais") as e:
            http.baixar(srv.url("/l.pdf"), parcial)
        assert not e.value.transitorio and not os.path.exists(parcial)

    def test_404_e_definitivo(self, srv, http, tmp_path):
        with pytest.raises(ErroRede) as e:
            http.baixar(srv.url("/nada.pdf"), str(tmp_path / "l.part"))
        assert e.value.status == 404 and len(srv.requisicoes) == 1

    def test_dominio_nao_permitido(self, http, tmp_path):
        with pytest.raises(ErroRede, match="não permitido"):
            http.baixar("http://localhost:1/x.pdf", str(tmp_path / "l.part"))
