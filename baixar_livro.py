"""Baixe Livros - baixa livros de domínio público/acesso aberto (Internet Archive e Project Gutenberg).

Janela com fila em lote (vários títulos de uma vez, downloads em paralelo, progresso, retomada) e uma
biblioteca pessoal em SQLite. Também funciona pela linha de comando:

    python baixar_livro.py "dom casmurro" "iracema"

Os livros ficam na pasta "Teste de Download" ao lado do programa (ou na indicada em --pasta).
O resultado de cada título aparece só no Log: verde = concluído, vermelho = não encontrado/falhou.
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox, scrolledtext, ttk

from arquivos import EXT_ABRIR
from biblioteca import Biblioteca
from fila import BAIXANDO, CONCLUIDO, EXISTIA, FilaDownloads, Item
from fontes import FONTES
from nucleo import Contexto, limpar_parciais_antigos
from rede import Http

try:
    import winsound
except ImportError:  # não-Windows
    winsound = None

PASTA_NOME = "Teste de Download"
BANCO_NOME = "biblioteca.sqlite3"


def pasta_base() -> str:
    if getattr(sys, "frozen", False):  # rodando como .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def criar_contexto(pasta: str, banco: str | None = None) -> Contexto:
    """O registro da biblioteca fica na própria pasta de livros: cada pasta tem o seu."""
    os.makedirs(pasta, exist_ok=True)
    return Contexto(pasta=pasta, http=Http(), fontes=FONTES,
                    biblioteca=Biblioteca(banco or os.path.join(pasta, BANCO_NOME)))


def formatar_tamanho(n: int | None) -> str:
    if not n:
        return ""
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def formatar_progresso(item: Item) -> str:
    if item.estado == BAIXANDO and item.total:
        return f"{item.percentual}% ({formatar_tamanho(item.baixado)})"
    if item.estado == BAIXANDO and item.baixado:
        return formatar_tamanho(item.baixado)
    return "100%" if item.estado in (CONCLUIDO, EXISTIA) else ""


# --- linha de comando ----------------------------------------------------------
def executar_cli(titulos: list[str], ctx: Contexto, paralelo: int = 3, saida=print) -> int:
    """Baixa os títulos sem janela. Retorna 0 se todos foram concluídos, 1 caso contrário."""
    trava = threading.Lock()

    def ao_log(item: Item, texto: str, tag: str | None) -> None:
        marca = {"ok": "[OK] ", "erro": "[ERRO] "}.get(tag or "", "")
        linha = f"[{item.id}] {marca}{texto}"
        with trava:
            try:
                saida(linha)
            except UnicodeEncodeError:   # terminal com codificação antiga (cp1252...): troca o que não imprime
                saida(linha.encode("ascii", "replace").decode("ascii"))

    fila = FilaDownloads(ctx, ao_log=ao_log, trabalhadores=paralelo)
    itens = fila.adicionar(titulos)
    try:
        fila.aguardar()
    except KeyboardInterrupt:
        fila.parar_tudo()
        fila.aguardar(10)
        return 130
    return 0 if itens and all(i.estado in (CONCLUIDO, EXISTIA) for i in itens) else 1


# --- janela --------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self, ctx: Contexto | None = None):
        super().__init__()
        self.title("Baixe Livros")
        self.geometry("780x720")
        self.minsize(700, 620)
        self.pasta = os.path.join(pasta_base(), PASTA_NOME)
        self.ctx = ctx or criar_contexto(self.pasta)
        self.pasta = self.ctx.pasta
        os.makedirs(self.pasta, exist_ok=True)
        limpar_parciais_antigos(self.pasta)
        self.eventos: queue.Queue = queue.Queue()
        self.fila = FilaDownloads(self.ctx, ao_mudar=lambda i: self.eventos.put(("item", i)),
                                  ao_log=lambda i, t, tag: self.eventos.put(("log", i, t, tag)))
        self._encerrando = False
        self._estava_ocupado = False
        self._silenciar = False
        self.protocol("WM_DELETE_WINDOW", self.fechar_janela)

        estilo = ttk.Style(self)
        if "vista" in estilo.theme_names():
            estilo.theme_use("vista")
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 18, "bold"))
        estilo.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#666666")
        estilo.configure("Info.TLabel", font=("Segoe UI", 10))
        estilo.configure("Acao.TButton", font=("Segoe UI", 10, "bold"), padding=5)

        corpo = ttk.Frame(self, padding=(20, 16))
        corpo.pack(fill="both", expand=True)
        ttk.Label(corpo, text="📚 Baixe Livros", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(corpo, text="Internet Archive e Project Gutenberg  •  livros em “" + PASTA_NOME + "”",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 10))

        self.abas = ttk.Notebook(corpo)
        self.abas.pack(fill="both", expand=True)
        self._montar_downloads()
        self._montar_biblioteca()
        self.atualizar_biblioteca()
        self.entrada.focus_set()
        self.after(100, self.processar_eventos)

    # --- aba Downloads -------------------------------------------------------
    def _montar_downloads(self):
        aba = ttk.Frame(self.abas, padding=10)
        self.abas.add(aba, text="Downloads")
        ttk.Label(aba, text="Títulos (um por linha) — Ctrl+Enter adiciona à fila", style="Info.TLabel").pack(anchor="w")
        self.entrada = tk.Text(aba, height=4, font=("Segoe UI", 11), wrap="word", undo=True)
        self.entrada.pack(fill="x", pady=(4, 8))
        self.entrada.bind("<Control-Return>", lambda _e: (self.adicionar(), "break")[1])

        botoes = ttk.Frame(aba)
        botoes.pack(fill="x")
        self.botao_add = ttk.Button(botoes, text="Adicionar à fila", style="Acao.TButton", command=self.adicionar)
        self.botao_add.pack(side="left")
        self.botao_parar = ttk.Button(botoes, text="Parar tudo", style="Acao.TButton", command=self.parar,
                                      state="disabled")
        self.botao_parar.pack(side="left", padx=6)
        self.botao_repetir = ttk.Button(botoes, text="Repetir falhas", command=self.repetir)
        self.botao_repetir.pack(side="left")
        ttk.Button(botoes, text="Abrir pasta", command=self.abrir_pasta).pack(side="right")

        colunas = ("titulo", "livro", "fonte", "estado", "progresso")
        self.tabela = ttk.Treeview(aba, columns=colunas, show="headings", height=7)
        for col, texto, larg in (("titulo", "Digitado", 150), ("livro", "Livro encontrado", 230), ("fonte", "Fonte", 100),
                                 ("estado", "Estado", 100), ("progresso", "Progresso", 110)):
            self.tabela.heading(col, text=texto)
            self.tabela.column(col, width=larg, anchor="w")
        self.tabela.pack(fill="x", pady=(10, 6))

        ttk.Label(aba, text="Log", style="Info.TLabel").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(aba, height=10, font=("Consolas", 9), state="disabled",
                                             background="#1e1e1e", foreground="#d4d4d4", relief="flat")
        self.log.tag_configure("ok", foreground="#3fb950", font=("Consolas", 9, "bold"))
        self.log.tag_configure("erro", foreground="#f85149", font=("Consolas", 9, "bold"))
        self.log.pack(fill="both", expand=True)

    # --- aba Biblioteca ------------------------------------------------------
    def _montar_biblioteca(self):
        aba = ttk.Frame(self.abas, padding=10)
        self.abas.add(aba, text="Biblioteca")
        linha = ttk.Frame(aba)
        linha.pack(fill="x")
        ttk.Label(linha, text="Buscar:", style="Info.TLabel").pack(side="left")
        self.filtro = tk.StringVar()
        self.filtro.trace_add("write", lambda *_: self.atualizar_biblioteca())
        ttk.Entry(linha, textvariable=self.filtro, font=("Segoe UI", 11)).pack(side="left", fill="x", expand=True, padx=6)
        self.total_lbl = ttk.Label(linha, text="", style="Sub.TLabel")
        self.total_lbl.pack(side="right")

        colunas = ("titulo", "autor", "fonte", "tamanho", "data")
        self.lista = ttk.Treeview(aba, columns=colunas, show="headings", selectmode="browse")
        for col, texto, larg in (("titulo", "Título", 250), ("autor", "Autor", 160), ("fonte", "Fonte", 100),
                                 ("tamanho", "Tamanho", 80), ("data", "Baixado em (UTC)", 130)):
            self.lista.heading(col, text=texto)
            self.lista.column(col, width=larg, anchor="w")
        self.lista.pack(fill="both", expand=True, pady=8)
        self.lista.bind("<Double-1>", lambda _e: self.abrir_livro())

        botoes = ttk.Frame(aba)
        botoes.pack(fill="x")
        ttk.Button(botoes, text="Abrir", style="Acao.TButton", command=self.abrir_livro).pack(side="left")
        ttk.Button(botoes, text="Abrir pasta", command=self.abrir_pasta).pack(side="left", padx=6)
        ttk.Button(botoes, text="Remover do registro", command=self.remover_registro).pack(side="left")
        ttk.Label(botoes, text="(o arquivo no disco não é apagado)", style="Sub.TLabel").pack(side="left", padx=8)

    def atualizar_biblioteca(self) -> None:
        livros = self.ctx.biblioteca.listar(self.filtro.get())
        self.lista.delete(*self.lista.get_children())
        for l in livros:
            self.lista.insert("", "end", iid=str(l.id), values=(
                l.titulo, l.autor, l.fonte, formatar_tamanho(l.tamanho), l.baixado_em.replace("T", " ").rstrip("Z")))
        self.total_lbl.configure(text=f"{len(livros)} livro(s)")

    def _livro_selecionado(self):
        sel = self.lista.selection()
        if not sel:
            return None
        return next((l for l in self.ctx.biblioteca.listar() if str(l.id) == sel[0]), None)

    def abrir_livro(self) -> None:
        livro = self._livro_selecionado()
        if livro:
            self._abrir_arquivo(os.path.join(self.pasta, os.path.basename(livro.arquivo)))

    def remover_registro(self) -> None:
        livro = self._livro_selecionado()
        if livro and messagebox.askyesno("Remover do registro",
                                         f"Remover “{livro.titulo}” da biblioteca?\n\nO arquivo no disco NÃO será apagado."):
            self.ctx.biblioteca.remover(livro.id)
            self.atualizar_biblioteca()

    # --- ações ---------------------------------------------------------------
    def escrever_log(self, texto: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {texto}\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def adicionar(self) -> None:
        titulos = [t for t in self.entrada.get("1.0", "end").splitlines() if t.strip()]
        if not titulos:
            return
        self.entrada.delete("1.0", "end")
        self._silenciar = False
        self._estava_ocupado = True   # os eventos trazem o item já atualizado: não dá para deduzir isso depois
        self.fila.adicionar(titulos)

    def parar(self) -> None:
        self._silenciar = True   # quem clicou em Parar já está olhando: sem aviso sonoro ao esvaziar
        self.fila.parar_tudo()
        self.botao_parar.state(["disabled"])

    def repetir(self) -> None:
        self._silenciar = False
        self._estava_ocupado = True
        self.fila.repetir_falhas()

    def abrir_pasta(self) -> None:
        os.makedirs(self.pasta, exist_ok=True)
        os.startfile(self.pasta)

    def _abrir_arquivo(self, caminho: str) -> None:
        if os.path.isfile(caminho) and os.path.splitext(caminho)[1].lower() in EXT_ABRIR:
            os.startfile(caminho)  # nunca abre executáveis/zip/rar

    def fechar_janela(self) -> None:
        self._encerrando = True
        self.withdraw()
        self.fila.parar_tudo()
        terminou = self.fila.aguardar(3)  # os parciais ficam guardados para retomar na próxima vez
        if terminou:   # com download ainda ativo, fechar o banco faria o registro falhar depois de publicar o arquivo
            try:
                self.ctx.biblioteca.fechar()
            except Exception:
                pass
        self.destroy()

    # --- eventos vindos das threads de trabalho -------------------------------
    def processar_eventos(self) -> None:
        try:
            while True:
                try:
                    ev = self.eventos.get_nowait()
                except queue.Empty:
                    break
                try:
                    if ev[0] == "item":
                        self._atualizar_item(ev[1])
                    else:
                        _, item, texto, tag = ev
                        prefixo = f"[{item.id}] " if len(self.fila.itens) > 1 else ""
                        self.escrever_log(prefixo + texto, tag)
                except Exception:  # um erro ao exibir não pode parar a fila de eventos
                    try:
                        self.escrever_log("ERRO INTERNO:\n" + traceback.format_exc(), "erro")
                    except Exception:
                        pass
            self._atualizar_botoes()
        finally:
            if not self._encerrando:
                self.after(100, self.processar_eventos)

    def _atualizar_item(self, item: Item) -> None:
        valores = (item.titulo, item.livro, item.fonte, item.estado, formatar_progresso(item))
        iid = str(item.id)
        if self.tabela.exists(iid):
            self.tabela.item(iid, values=valores)
        else:
            self.tabela.insert("", "end", iid=iid, values=valores)
        if item.estado in (CONCLUIDO, EXISTIA):
            self.atualizar_biblioteca()

    def _atualizar_botoes(self) -> None:
        ocupado = self.fila.ativos() > 0
        self.botao_parar.state(["!disabled"] if ocupado else ["disabled"])
        if self._estava_ocupado and not ocupado:
            if not self._silenciar:
                self.avisar()
            self._silenciar = False
        self._estava_ocupado = ocupado

    def avisar(self) -> None:
        """Som + traz a janela para a frente quando a fila termina (o resultado de cada título fica no Log)."""
        if winsound:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        self.bell()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(500, lambda: self.attributes("-topmost", False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Baixa livros de acesso aberto (Internet Archive e Project Gutenberg).")
    ap.add_argument("titulos", nargs="*", help="títulos a baixar; sem argumentos abre a janela")
    ap.add_argument("--pasta", help=f"pasta de destino (padrão: “{PASTA_NOME}” ao lado do programa)")
    ap.add_argument("--paralelo", type=int, default=3, choices=range(1, 9), metavar="1-8",
                    help="downloads simultâneos (padrão 3)")
    args = ap.parse_args(argv)
    if args.titulos:
        pasta = args.pasta or os.path.join(pasta_base(), PASTA_NOME)
        ctx = criar_contexto(pasta)
        try:
            return executar_cli(args.titulos, ctx, args.paralelo)
        finally:
            ctx.biblioteca.fechar()
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
