"""Testes de titulos."""
import pytest

import titulos as b


class TestTokens:
    def test_remove_acentos_pontuacao_e_caixa(self):
        assert b.tokens("Memórias Póstumas de Brás Cubas!") == ["memorias", "postumas", "de", "bras", "cubas"]

    def test_letras_especiais_nao_quebram_a_palavra(self):
        assert b.tokens("Søren Łukasz Æsop") == ["soren", "lukasz", "aesop"]

    def test_outros_alfabetos_sao_mantidos(self):
        assert b.tokens("Война и мир") == ["война", "и", "мир"]


class TestParecePeriodico:
    """Títulos reais devolvidos pelo Internet Archive para buscas como "1984", "jornal" e "revista"."""

    @pytest.mark.parametrize("titulo,autor,digitado", [
        ("Haryana Gazette, 1984-09-14, No. 40", "Government of Haryana", "1984"),           # data completa
        ("Jornal O Popular - 25/04/1984 - Capa", "Jornal O Popular", "1984"),
        ("Impact of tHe price hike in steel on the States (2-Aug-1984)", "Rajya Sabha Secretariat", "1984"),
        ("Jornal do Comércio, 22 out. 1948, n.14.884", "Orgão dos DIÁRIOS ASSOCIADOS", "jornal do comercio"),
        ("FADCA Beacon November/December 1984", "Florida Amateur Digital Communications", "1984"),  # mês + ano
        ("Deposition of JAMES CHANDLER BOWLING, December 19, 1984, CIP", "", "1984"),
        ("Softalk for the IBM PC v3n1 1984-06 Jun", "Softalk for the IBM PC", "1984"),     # volume/número
        ("Revista N°22 del Instituto de Estudios Genealógicos", "Instituto", "estudios"),
        ("Weekly Coal Production Mar. 17,1984", "U.s. Department Of Energy", "1984"),
        ("Jornal O Liberal", "Jornais da Paraíba (XIX e XX)", "liberal"),                  # nome começa com "Jornal"
        ("Revista Paradoxa 12", "Paradoxa Centro de Medios", "paradoxa"),
        ("Almanah Flacara 1984 pagina 228-287", "", "1984"),
        ("JORNAL O FUTURO #6", "PCBR", "jornal"),            # a palavra sozinha é categoria, não título: não isenta
        ("ACERVO JORNAL A TARDE — ARQUIVO PÚBLICO DO ESTADO DA BAHIA", "", "jornal"),   # achado no teste real
        ("O PRIMEIRO JORNAL PARAIBANO", "JOSÉ OZILDO DOS SANTOS", "jornal"),
        ("Entrevista Carlos Liscano", "Wilson Javier Cardozo", "revista"),   # "revista" ~ "entrevista" na tolerância a erros
        ("Devils Tower National Monument, Wyoming (1984)", "United States. National Park Service", "1984"),  # só número
        ("Corpo Analítico Das Revistas Juliana Souza", "Juliana Souza", "revistas"),
        ("Revista de Obras Públicas de Puerto Rico", "", "revista"),
        ("Industry Newsletter", "", "industry"),                                          # palavra forte em qualquer lugar
        ("Twenty First Report on Committee on Petitions", "Lok Sabha Secretariat", "petitions"),  # autor institucional
        ("Goma, Paul Ela (ed. 1984) V. 0.9.8 MMXII Pagina Alfa Beta", "", "1984"),         # título enorme para 1 palavra
    ])
    def test_descarta_jornais_revistas_documentos(self, titulo, autor, digitado):
        assert b.parece_periodico(titulo, autor, digitado) is True

    @pytest.mark.parametrize("titulo,autor,digitado", [
        ("Dom Casmurro", "Machado de Assis", "dom casmurro"),
        ("Dom Casmurro - Machado de Assis (1899)", "Machado de Assis", "dom casmurro"),     # um ano sozinho é normal
        ("Iracema (Versão em pt-PT)", "José de Alencar", "iracema"),
        ("1984", "George Orwell", "1984"),
        ("Fahrenheit 451", "Ray Bradbury", "fahrenheit 451"),
        ("The No. 1 Ladies' Detective Agency", "Alexander McCall Smith", "ladies detective agency"),
        ("A Journal of the Plague Year", "Daniel Defoe", "plague year"),                   # "journal" no meio: livro
        ("Diário de Anne Frank", "Anne Frank", "anne frank"),
        ("Memorias de José Garibaldi, volume II", "Garibaldi, Giuseppe", "garibaldi"),
        ("Brasil 1500-1822", "", "brasil"),                                                 # intervalo de anos: livro
        ("Jornal de um rebelde", "Sampaio, Albino Forjaz de", "jornal de um rebelde"),     # o usuário digitou a palavra
        ("Revista Paradoxa 12", "Paradoxa Centro de Medios", "revista paradoxa 12"),       # idem
        ("O Cortiço", "Aluísio Azevedo", "o cortico"),
        ("A Journal of the Plague Year", "Daniel Defoe", "journal plague year"),
        ("2001: A Space Odyssey", "Arthur C. Clarke", "2001"),                 # busca só por número: título curto serve
    ])
    def test_nao_descarta_livros(self, titulo, autor, digitado):
        assert b.parece_periodico(titulo, autor, digitado) is False


class TestBuscaDeCategoria:
    @pytest.mark.parametrize("digitado", ["jornal", "Revista", "jornais", "newsletter", "revistas jornal"])
    def test_palavra_de_categoria_nao_e_titulo_de_livro(self, digitado):
        assert b.eh_categoria(digitado) is True

    @pytest.mark.parametrize("digitado", ["jornal de um rebelde", "dom casmurro", "1984", "", "revista paradoxa"])
    def test_titulos_de_verdade_nao_sao_categoria(self, digitado):
        assert b.eh_categoria(digitado) is False


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
