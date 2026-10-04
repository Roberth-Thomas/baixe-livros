"""Fontes de livros com API aberta: Project Gutenberg (via Gutendex) e Internet Archive.

Cada fonte implementa `buscar(consulta, http, cancelado) -> list[Obra]` e
`arquivos(obra, http, cancelado) -> list[Arquivo]` (do formato preferido ao menos preferido).
Para somar outra fonte, basta criar uma classe com essa interface e incluí-la em `FONTES`.
"""
from __future__ import annotations

import re
from typing import Callable, NamedTuple
from urllib.parse import quote, quote_plus, urlencode

from erros import ErroRede
from rede import MAX_ARQUIVO, Http
from titulos import tokens


TENTATIVAS_BUSCA = 2   # buscas são rápidas e repetidas; falhar cedo deixa o app mais ágil
OPERADORES = {"and", "or", "not", "to"}


class Obra(NamedTuple):
    fonte: str          # identificador estável da fonte (ex.: "gutenberg")
    id: str
    titulo: str
    autor: str
    idioma: str
    licenca: str
    extra: tuple = ()   # dados específicos da fonte (ex.: formatos do Gutenberg)


class Arquivo(NamedTuple):
    url: str
    ext: str            # ".pdf", ".epub"...
    tamanho: int | None


def _lista(valor) -> list:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


def _texto(valor) -> str:
    return ", ".join(str(v) for v in _lista(valor)).strip()


# --- Project Gutenberg (Gutendex) --------------------------------------------
class Gutenberg:
    id = "gutenberg"
    nome = "Project Gutenberg"
    PREFERENCIA = (("application/epub+zip", ".epub"), ("application/x-mobipocket-ebook", ".mobi"),
                   ("text/plain", ".txt"))

    def buscar(self, consulta: str, http: Http, cancelado: Callable[[], bool] = lambda: False) -> list[Obra]:
        dados = http.get_json(f"https://gutendex.com/books/?search={quote_plus(consulta[:100])}", cancelado,
                              tentativas=TENTATIVAS_BUSCA)
        obras = []
        for b in dados.get("results", []):
            if b.get("copyright") is True or not b.get("id") or not b.get("title"):
                continue
            autores = ", ".join(a.get("name", "") for a in b.get("authors", []) if a.get("name"))
            idiomas = b.get("languages") or [""]
            obras.append(Obra(self.id, str(b["id"]), b["title"], autores, idiomas[0],
                              "Domínio público (Project Gutenberg)",
                              tuple((b.get("formats") or {}).items())))
        return obras

    def arquivos(self, obra: Obra, http: Http, cancelado: Callable[[], bool] = lambda: False) -> list[Arquivo]:
        formatos = dict(obra.extra)
        saida = []
        for mime, ext in self.PREFERENCIA:
            urls = [u for m, u in formatos.items() if m.startswith(mime) and http.permitida(u)]
            # prefere a variante com imagens; dentro dela, a ordem do site
            urls.sort(key=lambda u: ("noimages" in u, "images" not in u))
            if urls:
                saida.append(Arquivo(urls[0], ext, None))
        return saida


# --- Internet Archive ---------------------------------------------------------
class InternetArchive:
    id = "archive"
    nome = "Internet Archive"
    FORMATOS = (("Text PDF", ".pdf"), ("EPUB", ".epub"), ("Image Container PDF", ".pdf"))
    ID_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")

    def buscar(self, consulta: str, http: Http, cancelado: Callable[[], bool] = lambda: False) -> list[Obra]:
        # só letras/números minúsculos: a consulta nunca injeta sintaxe de busca; and/or/not saem para
        # não serem lidos como operadores se o servidor aceitar operadores em minúsculas
        palavras = [p for p in tokens(consulta) if p not in OPERADORES][:6]
        if not palavras:
            return []
        # palavras de 4+ letras casam por começo OU fim (tolera erro de digitação no início ou no fim)
        termos = " AND ".join(f"(*{p} OR {p}*)" if len(p) >= 4 else p for p in palavras)
        q = f"title:({termos}) AND mediatype:texts AND -access-restricted-item:true"
        params = [("q", q), ("rows", "15"), ("output", "json")]
        params += [("fl[]", c) for c in ("identifier", "title", "creator", "language", "licenseurl")]
        dados = http.get_json("https://archive.org/advancedsearch.php?" + urlencode(params), cancelado,
                              tentativas=TENTATIVAS_BUSCA)
        obras = []
        for d in (dados.get("response") or {}).get("docs", []):
            ident = str(d.get("identifier", ""))
            if not self.ID_VALIDO.match(ident) or not d.get("title"):
                continue
            obras.append(Obra(self.id, ident, _texto(d.get("title")), _texto(d.get("creator")),
                              (_lista(d.get("language")) or [""])[0], self._licenca(_texto(d.get("licenseurl")))))
        return obras

    @staticmethod
    def _licenca(url: str) -> str:
        if not url:
            return "licença não informada pelo Internet Archive"
        if "publicdomain" in url or "/zero/" in url:
            return "Domínio público"
        return f"Licença: {url}"

    def arquivos(self, obra: Obra, http: Http, cancelado: Callable[[], bool] = lambda: False) -> list[Arquivo]:
        if not self.ID_VALIDO.match(obra.id):
            raise ErroRede("identificador de item inválido")
        meta = http.get_json(f"https://archive.org/metadata/{quote(obra.id)}", cancelado)
        arquivos = [f for f in meta.get("files", []) if not f.get("private") and f.get("name")]
        saida = []
        for formato, ext in self.FORMATOS:
            for f in arquivos:
                if f.get("format") != formato:
                    continue
                tam = int(f["size"]) if str(f.get("size", "")).isdigit() else None
                if tam is not None and tam > MAX_ARQUIVO:
                    continue
                saida.append(Arquivo(f"https://archive.org/download/{quote(obra.id)}/{quote(f['name'])}", ext, tam))
                break
        return saida


FONTES = [InternetArchive(), Gutenberg()]
