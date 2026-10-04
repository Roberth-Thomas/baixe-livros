"""Fila de downloads em lote: vários títulos ao mesmo tempo, com progresso, parada e nova tentativa."""
from __future__ import annotations

import dataclasses
import queue
import threading
from dataclasses import dataclass, field
from typing import Callable

from erros import Cancelado, DownloadFalhou, ErroFontes, LivroNaoEncontrado
from nucleo import Contexto, processar

NA_FILA, BUSCANDO, BAIXANDO = "Na fila", "Buscando", "Baixando"
CONCLUIDO, EXISTIA = "Concluído", "Já na biblioteca"
NAO_ENCONTRADO, FALHOU, PARADO = "Não encontrado", "Falhou", "Parado"
ATIVOS = {NA_FILA, BUSCANDO, BAIXANDO}
REPETIVEIS = {NAO_ENCONTRADO, FALHOU, PARADO}


@dataclass(eq=False)
class Item:
    id: int
    titulo: str
    estado: str = NA_FILA
    baixado: int = 0
    total: int | None = None
    livro: str = ""
    fonte: str = ""
    arquivo: str = ""
    cancelar: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def percentual(self) -> int | None:
        return int(self.baixado * 100 / self.total) if self.total else None


class FilaDownloads:
    """`ao_mudar(item)` e `ao_log(item, texto, tag)` são chamados a partir das threads de trabalho
    (a interface deve repassá-los para a thread principal)."""

    def __init__(self, base: Contexto, ao_mudar: Callable[[Item], None] = lambda item: None,
                 ao_log: Callable[[Item, str, str | None], None] = lambda item, texto, tag: None,
                 trabalhadores: int = 3):
        self.base, self.ao_mudar, self.ao_log = base, ao_mudar, ao_log
        self.itens: list[Item] = []
        self._q: queue.Queue[Item] = queue.Queue()
        self._trava = threading.Lock()
        self._proximo = 1
        self._trabalhadores = trabalhadores
        self._threads: list[threading.Thread] = []

    # --- API ----------------------------------------------------------------
    def adicionar(self, titulos: list[str]) -> list[Item]:
        novos = []
        with self._trava:
            for t in titulos:
                t = " ".join(t.split())
                if not t:
                    continue
                item = Item(self._proximo, t)
                self._proximo += 1
                self.itens.append(item)
                novos.append(item)
            self._garantir_trabalhadores()
        for item in novos:
            self._q.put(item)
            self._avisar(self.ao_mudar, item)
        return novos

    def parar_tudo(self) -> None:
        for item in list(self.itens):
            if item.estado in ATIVOS:
                item.cancelar.set()

    def parar(self, item: Item) -> None:
        item.cancelar.set()

    def repetir_falhas(self) -> list[Item]:
        refeitos = []
        for item in list(self.itens):
            if item.estado in REPETIVEIS:
                item.cancelar = threading.Event()
                item.estado, item.baixado, item.total, item.livro, item.fonte, item.arquivo = NA_FILA, 0, None, "", "", ""
                refeitos.append(item)
                self._q.put(item)
                self._avisar(self.ao_mudar, item)
        with self._trava:
            self._garantir_trabalhadores()
        return refeitos

    def aguardar(self, timeout: float | None = None) -> bool:
        """Espera a fila esvaziar. Retorna False se estourar o `timeout`."""
        if timeout is None:
            self._q.join()
            return True
        pronto = threading.Event()
        threading.Thread(target=lambda: (self._q.join(), pronto.set()), daemon=True).start()
        return pronto.wait(timeout)

    def ativos(self) -> int:
        return sum(1 for i in self.itens if i.estado in ATIVOS)

    # --- internos -------------------------------------------------------------
    def _garantir_trabalhadores(self) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        while len(self._threads) < self._trabalhadores:
            t = threading.Thread(target=self._trabalhar, daemon=True)
            t.start()
            self._threads.append(t)

    def _mudar(self, item: Item, estado: str | None = None) -> None:
        if estado:
            item.estado = estado
        self._avisar(self.ao_mudar, item)

    def _log(self, item: Item, texto: str, tag: str | None = None) -> None:
        self._avisar(self.ao_log, item, texto, tag)

    @staticmethod
    def _avisar(callback, *args) -> None:
        """Quem escuta (janela, terminal) pode falhar — ex.: terminal sem suporte a ✔ —, mas isso nunca
        pode interromper um download nem matar a thread de trabalho."""
        try:
            callback(*args)
        except Exception:
            pass

    def _trabalhar(self) -> None:
        while True:
            item = self._q.get()
            try:
                self._executar(item)
            except Exception as e:  # nunca deixa uma thread de trabalho morrer
                self._log(item, f"✖ Erro inesperado: {type(e).__name__}: {e}", "erro")
                self._mudar(item, FALHOU)
            finally:
                self._q.task_done()

    def _executar(self, item: Item) -> None:
        if item.cancelar.is_set():
            self._log(item, "■ Interrompido antes de começar.", None)
            return self._mudar(item, PARADO)

        def estado(texto: str) -> None:
            self._mudar(item, texto)

        def progresso(baixado: int, total: int | None) -> None:
            item.baixado, item.total = baixado, total
            self._avisar(self.ao_mudar, item)

        ctx = dataclasses.replace(
            self.base, log=lambda msg, tag=None: self._log(item, msg, tag), estado=estado, progresso=progresso)
        self._log(item, f"--- Nova busca: “{item.titulo}” ---", None)
        self._mudar(item, BUSCANDO)
        try:
            r = processar(item.titulo, ctx, item.cancelar.is_set)
        except Cancelado:
            self._log(item, "■ Busca interrompida.", None)
            return self._mudar(item, PARADO)
        except LivroNaoEncontrado:
            self._log(item, f"✖ Livro não encontrado: “{item.titulo}”", "erro")
            return self._mudar(item, NAO_ENCONTRADO)
        except DownloadFalhou as e:
            self._log(item, f"✖ Livro encontrado (“{e.nome}”), mas o download não foi possível: {e.motivo}", "erro")
            return self._mudar(item, FALHOU)
        except ErroFontes as e:
            self._log(item, f"✖ As fontes de livros não responderam: {e}", "erro")
            return self._mudar(item, FALHOU)
        item.livro, item.fonte, item.arquivo = r.nome, r.fonte, r.arquivo
        if r.ja_existia:
            self._log(item, f"✔ Concluído: “{r.nome}” já estava na biblioteca ({r.arquivo}); não foi baixado de novo.", "ok")
            return self._mudar(item, EXISTIA)
        self._log(item, f"✔ Concluído: “{r.nome}” salvo como {r.arquivo}", "ok")
        self._mudar(item, CONCLUIDO)
