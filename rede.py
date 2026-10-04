"""Cliente HTTP mínimo (só biblioteca padrão) com lista de domínios permitidos e download com retomada."""
from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlsplit

from erros import Cancelado, ErroRede

USER_AGENT = "BaixeLivros/2.0 (+https://github.com/Roberth-Thomas/baixe-livros)"
HOSTS_PADRAO = ("archive.org", "gutenberg.org", "gutendex.com")
MAX_JSON = 5 * 1024 * 1024
MAX_ARQUIVO = 1024 ** 3   # 1 GiB: acima disso o download é recusado
BLOCO = 64 * 1024


def host_permitido(url: str, hosts: tuple[str, ...], esquemas: tuple[str, ...] = ("https",)) -> bool:
    """Só esquemas e domínios (ou subdomínios) da lista; `archive.org.evil.com` não passa."""
    p = urlsplit(url or "")
    host = (p.hostname or "").lower()
    return p.scheme in esquemas and any(host == d or host.endswith("." + d) for d in hosts)


class _Redirecionamento(urllib.request.HTTPRedirectHandler):
    """Recusa redirecionamentos para fora dos domínios permitidos."""

    def __init__(self, http: "Http"):
        self.http = http

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.http.permitida(newurl):
            raise ErroRede(f"redirecionamento para endereço não permitido ({urlsplit(newurl).hostname})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _verificar(cancelado: Callable[[], bool]) -> None:
    if cancelado():
        raise Cancelado()


class Http:
    def __init__(self, hosts: tuple[str, ...] = HOSTS_PADRAO, esquemas: tuple[str, ...] = ("https",),
                 timeout: float = 25, tentativas: int = 3, espera: float = 1.5):
        self.hosts, self.esquemas = hosts, esquemas
        self.timeout, self.tentativas, self.espera = timeout, tentativas, espera
        self._opener = urllib.request.build_opener(_Redirecionamento(self))

    def permitida(self, url: str) -> bool:
        return host_permitido(url, self.hosts, self.esquemas)

    # --- baixo nível ------------------------------------------------------
    def _abrir(self, url: str, cabecalhos: dict | None = None):
        if not self.permitida(url):
            raise ErroRede(f"endereço não permitido: {urlsplit(url).hostname or url}")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                                                   **(cabecalhos or {})})
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            transitorio = e.code == 429 or e.code >= 500
            raise ErroRede(f"o servidor respondeu HTTP {e.code}", status=e.code, transitorio=transitorio) from e
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise ErroRede(f"falha de conexão ({getattr(e, 'reason', e)})", transitorio=True) from e

    def _dormir(self, segundos: float, cancelado: Callable[[], bool]) -> None:
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            _verificar(cancelado)
            time.sleep(min(0.1, max(0.0, fim - time.monotonic())))

    def _com_tentativas(self, acao: Callable[[], object], cancelado: Callable[[], bool],
                        tentativas: int | None = None):
        maximo = tentativas or self.tentativas
        for n in range(1, maximo + 1):
            _verificar(cancelado)
            try:
                return acao()
            except ErroRede as e:
                if not e.transitorio or n == maximo:
                    raise
                self._dormir(self.espera * n, cancelado)

    # --- JSON -------------------------------------------------------------
    def get_json(self, url: str, cancelado: Callable[[], bool] = lambda: False, tentativas: int | None = None):
        def uma_vez():
            with self._abrir(url) as resp:
                try:
                    corpo = resp.read(MAX_JSON + 1)
                except (OSError, http.client.HTTPException) as e:
                    raise ErroRede(f"conexão interrompida ({e})", transitorio=True) from e
            if len(corpo) > MAX_JSON:
                raise ErroRede("resposta grande demais")
            try:
                return json.loads(corpo)
            except ValueError as e:
                raise ErroRede("resposta inválida (não é JSON)") from e
        return self._com_tentativas(uma_vez, cancelado, tentativas)

    # --- download com retomada ---------------------------------------------
    def baixar(self, url: str, parcial: str, progresso: Callable[[int, int | None], None] | None = None,
               cancelado: Callable[[], bool] = lambda: False) -> int:
        """Baixa `url` em `parcial`. Se o arquivo parcial existir e o servidor aceitar `Range`,
        continua de onde parou; ao cancelar, o parcial é mantido para retomar depois."""
        return self._com_tentativas(lambda: self._baixar_uma_vez(url, parcial, progresso, cancelado), cancelado)

    @staticmethod
    def _ler_meta(caminho: str) -> dict:
        try:
            with open(caminho, encoding="utf8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _apagar(*caminhos: str) -> None:
        for c in caminhos:
            try:
                os.remove(c)
            except OSError:
                pass

    def _baixar_uma_vez(self, url, parcial, progresso, cancelado) -> int:
        meta_path = parcial + ".json"
        existente = os.path.getsize(parcial) if os.path.exists(parcial) else 0
        meta = self._ler_meta(meta_path)
        cab: dict[str, str] = {}
        if existente and meta.get("url") == url and meta.get("validador"):
            cab = {"Range": f"bytes={existente}-", "If-Range": meta["validador"]}
        else:
            existente = 0
        try:
            resp = self._abrir(url, cab)
        except ErroRede as e:
            if e.status == 416:  # retomada impossível: recomeça do zero na próxima tentativa
                self._apagar(parcial, meta_path)
                raise ErroRede("retomada recusada; recomeçando", status=416, transitorio=True) from e
            raise
        with resp:
            validador = resp.headers.get("ETag") or resp.headers.get("Last-Modified") or ""
            if resp.status == 206:
                m = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", resp.headers.get("Content-Range", ""))
                if not m or int(m.group(1)) != existente:
                    self._apagar(parcial, meta_path)
                    raise ErroRede("resposta de retomada inesperada; recomeçando", transitorio=True)
                total = int(m.group(3)) if m.group(3) != "*" else None
                modo = "ab"
            else:
                existente, modo = 0, "wb"
                tam = resp.headers.get("Content-Length", "")
                total = int(tam) if tam.isdigit() else None
            if (total or 0) > MAX_ARQUIVO:
                raise ErroRede("arquivo grande demais para baixar")
            with open(meta_path, "w", encoding="utf8") as f:
                json.dump({"url": url, "validador": validador}, f)
            baixado = existente
            with open(parcial, modo) as saida:
                while True:
                    _verificar(cancelado)
                    try:
                        bloco = resp.read(BLOCO)
                    except (OSError, http.client.HTTPException) as e:
                        raise ErroRede(f"conexão interrompida ({e})", transitorio=True) from e
                    if not bloco:
                        break
                    saida.write(bloco)
                    baixado += len(bloco)
                    if baixado > MAX_ARQUIVO:
                        raise ErroRede("arquivo grande demais para baixar")
                    if progresso:
                        progresso(baixado, total)
        if total is not None and baixado != total:
            raise ErroRede(f"download incompleto ({baixado} de {total} bytes)", transitorio=True)
        self._apagar(meta_path)
        return baixado
