"""Dublês de teste: fontes e cliente HTTP falsos, e servidor HTTP local de verdade."""
from __future__ import annotations

import http.server
import re
import threading
import time

from biblioteca import Biblioteca
from erros import Cancelado
from fontes import Arquivo, Obra
from nucleo import Contexto

PDF = b"%PDF-1.4\n" + b"conteudo de teste " * 120
EPUB = b"PK\x03\x04" + b"conteudo epub " * 120


def obra(fonte="a", id="1", titulo="Dom Casmurro", autor="Machado de Assis", idioma="pt",
         licenca="Domínio público") -> Obra:
    return Obra(fonte, id, titulo, autor, idioma, licenca)


def arq(url="https://x.test/a.pdf", ext=".pdf") -> Arquivo:
    return Arquivo(url, ext, None)


class FakeHttp:
    """Substitui rede.Http: `baixar` grava o conteúdo (bytes) definido por URL; Exception é levantada."""

    def __init__(self, conteudos: dict | None = None):
        self.conteudos = conteudos or {}
        self.baixados: list[str] = []
        self.antes = None   # gancho opcional: antes(url, cancelado)
        self._trava = threading.Lock()

    def permitida(self, url: str) -> bool:
        return True

    def baixar(self, url, parcial, progresso=None, cancelado=lambda: False):
        with self._trava:
            self.baixados.append(url)
        if self.antes:
            self.antes(url, cancelado)
        c = self.conteudos[url]
        if isinstance(c, Exception):
            raise c
        with open(parcial, "wb") as f:
            f.write(c)
        if progresso:
            progresso(len(c), len(c))
        return len(c)


class FakeFonte:
    """Fonte falsa. `obras`: lista fixa ou {consulta: lista}; `arquivos`: {id_da_obra: lista | Exception}."""

    def __init__(self, id="a", nome="Fonte A", obras=None, arquivos=None, erro=None, bloquear=None):
        self.id, self.nome = id, nome
        self._obras, self._arquivos, self.erro, self.bloquear = obras or [], arquivos or {}, erro, bloquear
        self.consultas: list[str] = []

    def buscar(self, consulta, http, cancelado=lambda: False):
        self.consultas.append(consulta)
        if self.bloquear:
            limite = time.monotonic() + 8     # se uma regressão deixar de esperar só `graca`, o teste falha em vez de travar
            while not self.bloquear.is_set() and time.monotonic() < limite:
                if cancelado():
                    raise Cancelado()   # como o Http real: cancelar é uma exceção, não uma lista vazia
                time.sleep(0.02)
        if self.erro:
            raise self.erro
        return list(self._obras.get(consulta, [])) if isinstance(self._obras, dict) else list(self._obras)

    def arquivos(self, obra, http, cancelado=lambda: False):
        r = self._arquivos.get(obra.id, [])
        if isinstance(r, Exception):
            raise r
        return r


def contexto(tmp_path, fontes, http=None, **kw) -> Contexto:
    ctx = Contexto(pasta=str(tmp_path / "livros"), http=http or FakeHttp(), fontes=fontes,
                   biblioteca=Biblioteca(), **kw)
    ctx.logs = []
    ctx.log = lambda msg, tag=None: ctx.logs.append((msg, tag))
    return ctx


class Servidor:
    """Servidor HTTP local. `rotas[caminho]` é um dict com: corpo, etag, range (bool), status, redirect,
    falhar_primeiras (n respostas 503 antes de servir), cortar_em/cortar_vezes (corta a conexão no meio)."""

    def __init__(self):
        self.rotas: dict[str, dict] = {}
        self.requisicoes: list[tuple[str, dict]] = []
        srv = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                caminho = self.path.split("?")[0]
                srv.requisicoes.append((self.path, dict(self.headers)))
                cfg = srv.rotas.get(caminho)
                if cfg is None:
                    return self._enviar(404, b"nao achei")
                if cfg.get("falhar_primeiras", 0) > 0:
                    cfg["falhar_primeiras"] -= 1
                    return self._enviar(503, b"ocupado")
                if "redirect" in cfg:
                    self.send_response(302)
                    self.send_header("Location", cfg["redirect"])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if cfg.get("status", 200) != 200:
                    return self._enviar(cfg["status"], b"erro")
                corpo, etag = cfg["corpo"], cfg.get("etag", '"v1"')
                inicio = 0
                rng, if_range = self.headers.get("Range"), self.headers.get("If-Range")
                if rng and cfg.get("range", True) and (not if_range or if_range == etag):
                    inicio = int(re.match(r"bytes=(\d+)-", rng).group(1))
                    if inicio >= len(corpo):
                        return self._enviar(416, b"")
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {inicio}-{len(corpo) - 1}/{len(corpo)}")
                else:
                    inicio = 0
                    self.send_response(200)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", str(len(corpo) - inicio))
                self.end_headers()
                fatia = corpo[inicio:]
                if cfg.get("cortar_vezes", 0) > 0:
                    cfg["cortar_vezes"] -= 1
                    fatia = fatia[:cfg["cortar_em"]]
                    self.wfile.write(fatia)
                    self.wfile.flush()
                    self.close_connection = True
                    return
                try:
                    for i in range(0, len(fatia), 16384):
                        self.wfile.write(fatia[i:i + 16384])
                        if cfg.get("pausa"):
                            time.sleep(cfg["pausa"])
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _enviar(self, status, corpo):
                self.send_response(status)
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.porta = self.httpd.server_address[1]
        self._thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True)
        self._thread.start()

    def url(self, caminho: str) -> str:
        return f"http://127.0.0.1:{self.porta}{caminho}"

    def parar(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
