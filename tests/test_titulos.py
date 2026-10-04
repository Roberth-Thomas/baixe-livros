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
