"""Exceções do Baixe Livros."""
from __future__ import annotations


class Cancelado(Exception):
    """O usuário pediu para parar."""


class LivroNaoEncontrado(Exception):
    """Nenhuma obra compatível com o título digitado."""


class BuscaIncompleta(Exception):
    """Nada compatível foi achado, mas parte das fontes falhou ou demorou demais: o livro pode existir.
    A mensagem lista as fontes que não responderam e o motivo."""


class DownloadFalhou(Exception):
    """A obra foi encontrada, mas nenhum download pôde ser concluído."""

    def __init__(self, nome: str, motivo: str):
        super().__init__(f"{nome}: {motivo}")
        self.nome, self.motivo = nome, motivo


class ErroFontes(Exception):
    """Todas as fontes de livros falharam (sem internet, fora do ar ou bloqueando o acesso)."""


class FalhaDownload(Exception):
    """Falha ao baixar UM arquivo (com o motivo em português)."""


class TipoNaoPermitido(FalhaDownload):
    """O servidor entregou um arquivo de tipo não permitido (ex.: .exe)."""


class ErroRede(FalhaDownload):
    """Falha de rede/HTTP. `transitorio` indica se vale tentar de novo."""

    def __init__(self, mensagem: str, *, status: int | None = None, transitorio: bool = False):
        super().__init__(mensagem)
        self.status, self.transitorio = status, transitorio
