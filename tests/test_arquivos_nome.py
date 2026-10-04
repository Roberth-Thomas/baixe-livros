"""Testes de arquivos."""
import pytest

import arquivos as b


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
