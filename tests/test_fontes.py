"""Testes das fontes (Internet Archive e Project Gutenberg) com respostas JSON simuladas."""
import pytest

import fontes as f
from erros import ErroRede
from fontes import Arquivo, Gutenberg, InternetArchive, Obra


class HttpJson:
    """Responde get_json por trecho da URL e guarda as URLs pedidas."""

    def __init__(self, respostas: dict, permitido=lambda u: True):
        self.respostas, self.urls, self.permitido = respostas, [], permitido

    def permitida(self, url):
        return self.permitido(url)

    def get_json(self, url, cancelado=lambda: False, tentativas=None):
        self.urls.append((url, tentativas))
        for trecho, r in self.respostas.items():
            if trecho in url:
                if isinstance(r, Exception):
                    raise r
                return r
        raise ErroRede("sem rota", status=404)


GUT = {"count": 3, "results": [
    {"id": 55752, "title": "Dom Casmurro", "authors": [{"name": "Machado de Assis"}], "languages": ["pt"],
     "copyright": False, "formats": {
         "text/html": "https://www.gutenberg.org/ebooks/55752.html.images",
         "application/epub+zip": "https://www.gutenberg.org/ebooks/55752.epub.noimages",
         "application/x-mobipocket-ebook": "https://www.gutenberg.org/ebooks/55752.kindle.images",
         "text/plain; charset=us-ascii": "https://www.gutenberg.org/ebooks/55752.txt.utf-8"}},
    {"id": 1, "title": "Livro com Direitos", "authors": [], "languages": ["en"], "copyright": True, "formats": {}},
    {"id": None, "title": "Sem id", "authors": [], "languages": [], "formats": {}},
]}


class TestGutenberg:
    def test_busca_ignora_copyright_e_itens_incompletos(self):
        h = HttpJson({"gutendex.com": GUT})
        obras = Gutenberg().buscar("dom casmurro", h)
        assert [(o.fonte, o.id, o.titulo, o.autor, o.idioma) for o in obras] == [
            ("gutenberg", "55752", "Dom Casmurro", "Machado de Assis", "pt")]
        assert "Domínio público" in obras[0].licenca

    def test_consulta_vai_codificada_e_com_poucas_tentativas(self):
        h = HttpJson({"gutendex.com": {"results": []}})
        Gutenberg().buscar('a b&c=d "e"', h)
        url, tent = h.urls[0]
        assert url.startswith("https://gutendex.com/books/?search=") and "&c=d" not in url and tent == f.TENTATIVAS_BUSCA

    def test_arquivos_em_ordem_de_preferencia(self):
        obra = Gutenberg().buscar("x", HttpJson({"gutendex": GUT}))[0]
        arqs = Gutenberg().arquivos(obra, HttpJson({}))
        assert [a.ext for a in arqs] == [".epub", ".mobi", ".txt"]
        assert arqs[0].url == "https://www.gutenberg.org/ebooks/55752.epub.noimages"

    def test_prefere_a_variante_com_imagens(self):
        o = Obra("gutenberg", "9", "T", "", "pt", "", (
            ("application/epub+zip", "https://www.gutenberg.org/ebooks/9.epub.noimages"),
            ("application/epub+zip", "https://www.gutenberg.org/ebooks/9.epub3.images")))
        assert Gutenberg().arquivos(o, HttpJson({}))[0].url.endswith("epub3.images")

    def test_descarta_urls_de_dominios_nao_permitidos(self):
        o = Obra("gutenberg", "9", "T", "", "pt", "", (("application/epub+zip", "https://evil.com/x.epub"),))
        assert Gutenberg().arquivos(o, HttpJson({}, permitido=lambda u: "gutenberg.org" in u)) == []


IA = {"response": {"numFound": 4, "docs": [
    {"identifier": "domcasmurro_1899", "title": "Dom Casmurro", "creator": "Machado de Assis", "language": "por",
     "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"},
    {"identifier": "dom-casmurro-2ed", "title": ["Dom Casmurro 2ed"], "creator": ["A", "B"], "language": ["por", "eng"]},
    {"identifier": "../../etc/passwd", "title": "Malicioso"},
    {"identifier": "sem_titulo"},
]}}


class TestInternetArchive:
    def test_busca_filtra_ids_invalidos_e_normaliza_campos(self):
        obras = InternetArchive().buscar("dom casmurro", HttpJson({"advancedsearch": IA}))
        assert [o.id for o in obras] == ["domcasmurro_1899", "dom-casmurro-2ed"]
        assert obras[0].licenca == "Domínio público"
        assert (obras[1].titulo, obras[1].autor, obras[1].idioma) == ("Dom Casmurro 2ed", "A, B", "por")
        assert "não informada" in obras[1].licenca

    def test_licenca_cc_mostra_a_url(self):
        assert "by-nc-sa" in InternetArchive._licenca("http://creativecommons.org/licenses/by-nc-sa/3.0/br/")

    def test_consulta_so_tem_letras_e_numeros_nunca_injeta_sintaxe(self):
        h = HttpJson({"advancedsearch": IA})
        InternetArchive().buscar('dom" OR title:(* ) AND -mediatype:texts \\ casmurro', h)
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(h.urls[0][0]).query)["q"][0]
        # tudo que o usuário digitou virou palavra solta; os únicos operadores são os que o app monta
        assert q == ("title:(dom AND (*title OR title*) AND (*mediatype OR mediatype*) AND (*texts OR texts*) "
                     "AND (*casmurro OR casmurro*)) AND mediatype:texts AND -access-restricted-item:true")
        assert '"' not in q and "\\" not in q and "-mediatype" not in q

    def test_operadores_digitados_pelo_usuario_sao_descartados(self):
        h = HttpJson({"advancedsearch": IA})
        InternetArchive().buscar("Dom AND Casmurro or NOT x", h)
        from urllib.parse import parse_qs, urlsplit
        assert "title:(dom AND (*casmurro OR casmurro*) AND x)" in parse_qs(urlsplit(h.urls[0][0]).query)["q"][0]

    def test_palavras_curtas_sem_curinga(self):
        h = HttpJson({"advancedsearch": IA})
        InternetArchive().buscar("dom ads", h)
        from urllib.parse import parse_qs, urlsplit
        assert "title:(dom AND ads)" in parse_qs(urlsplit(h.urls[0][0]).query)["q"][0]

    def test_consulta_sem_palavras_nao_pede_nada(self):
        h = HttpJson({})
        assert InternetArchive().buscar("???", h) == [] and h.urls == []

    META = {"files": [
        {"name": "capa.jpg", "format": "Item Tile", "size": "100"},
        {"name": "livro.epub", "format": "EPUB", "size": "385492"},
        {"name": "scan lento.pdf", "format": "Image Container PDF", "size": "9000000"},
        {"name": "livro (texto).pdf", "format": "Text PDF", "size": "31346440"},
        {"name": "privado.pdf", "format": "Text PDF", "size": "10", "private": "true"},
    ]}

    def test_arquivos_pdf_de_texto_primeiro_e_urls_codificadas(self):
        o = Obra("archive", "domcasmurro_1899", "T", "", "por", "")
        arqs = InternetArchive().arquivos(o, HttpJson({"/metadata/domcasmurro_1899": self.META}))
        assert [(a.ext, a.tamanho) for a in arqs] == [(".pdf", 31346440), (".epub", 385492), (".pdf", 9000000)]
        assert arqs[0].url == "https://archive.org/download/domcasmurro_1899/livro%20%28texto%29.pdf"

    def test_ignora_arquivos_acima_do_limite(self, monkeypatch):
        monkeypatch.setattr(f, "MAX_ARQUIVO", 1_000_000)
        o = Obra("archive", "x", "T", "", "", "")
        arqs = InternetArchive().arquivos(o, HttpJson({"/metadata/x": self.META}))
        assert [a.ext for a in arqs] == [".epub"]

    def test_item_sem_arquivos_de_livro(self):
        o = Obra("archive", "x", "T", "", "", "")
        assert InternetArchive().arquivos(o, HttpJson({"/metadata/x": {"files": [{"name": "a.jpg", "format": "JPEG"}]}})) == []

    def test_id_invalido_e_recusado(self):
        o = Obra("archive", "../x", "T", "", "", "")
        with pytest.raises(ErroRede):
            InternetArchive().arquivos(o, HttpJson({}))


def test_fontes_padrao_tem_a_interface_esperada():
    ids = [x.id for x in f.FONTES]
    assert len(ids) == len(set(ids)) >= 2
    for x in f.FONTES:
        assert x.nome and callable(x.buscar) and callable(x.arquivos)
    assert isinstance(Arquivo("u", ".pdf", None), tuple)
