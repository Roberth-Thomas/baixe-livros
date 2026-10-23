"""Núcleo: busca um título nas fontes, escolhe o melhor candidato, baixa, valida e registra na biblioteca."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, NamedTuple

from arquivos import publicar, sha256_arquivo, validar_arquivo
from biblioteca import Biblioteca
from erros import (BuscaIncompleta, Cancelado, DownloadFalhou, ErroFontes, ErroRede, FalhaDownload,
                   LivroNaoEncontrado, TipoNaoPermitido)
from fontes import Arquivo, Obra
from rede import Http
from titulos import pontuar, tokens, variantes_busca

PASTA_PARCIAIS = ".baixando"
MAX_CANDIDATOS = 5       # obras tentadas por título
MAX_ARQUIVOS = 3         # formatos tentados por obra
BONUS_IDIOMA = 0.10
IDIOMAS_PADRAO = ("pt", "por", "pt-br")


class Resultado(NamedTuple):
    nome: str          # título da obra
    arquivo: str       # nome do arquivo na pasta de downloads
    ja_existia: bool
    fonte: str
    licenca: str


class Candidato(NamedTuple):
    nota: float
    obra: Obra


def _log_nulo(msg: str, tag: str | None = None) -> None:
    pass


@dataclass
class Contexto:
    pasta: str
    http: Http
    fontes: list
    biblioteca: Biblioteca
    log: Callable[[str, str | None], None] = _log_nulo
    progresso: Callable[[int, int | None], None] = lambda baixado, total: None
    estado: Callable[[str], None] = lambda texto: None
    idiomas: tuple = IDIOMAS_PADRAO
    graca: float = 10.0      # s que as fontes lentas ganham depois que a primeira responde
    _travas: dict = field(default_factory=dict, repr=False)
    _guarda: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def trava_da_obra(self, obra: Obra) -> threading.Lock:
        """Uma trava por obra: dois itens da fila pedindo o mesmo livro não baixam duas vezes."""
        with self._guarda:
            return self._travas.setdefault((obra.fonte, obra.id), threading.Lock())


def _autor_util(obra: Obra) -> str:
    """O autor, a menos que seja vazio ou já esteja contido no título (alguns itens repetem o título como autor)."""
    autor = obra.autor.strip()
    if not autor or set(tokens(autor)) <= set(tokens(obra.titulo)):
        return ""
    return autor


def _verificar(cancelado: Callable[[], bool]) -> None:
    if cancelado():
        raise Cancelado()


def _consultar_fontes(consulta: str, ctx: Contexto, cancelado: Callable[[], bool]):
    """Consulta todas as fontes em paralelo. Retorna (obras, {nome_da_fonte: erro})."""
    resultados: dict[str, object] = {}

    def pedir(fonte):
        try:
            resultados[fonte.id] = fonte.buscar(consulta, ctx.http, cancelado)
        except (ErroRede, FalhaDownload, Cancelado) as e:   # Cancelado é guardado, mas NÃO é falha de fonte
            resultados[fonte.id] = e
        except Exception as e:  # uma fonte com defeito não pode derrubar as outras
            resultados[fonte.id] = ErroRede(f"resposta inesperada ({type(e).__name__})")

    threads = [threading.Thread(target=pedir, args=(f,), daemon=True) for f in ctx.fontes]
    for t in threads:
        t.start()
    primeira = None
    while any(t.is_alive() for t in threads):
        _verificar(cancelado)
        # só uma resposta de verdade (não um erro) dá início ao prazo das fontes lentas
        if any(not isinstance(r, Exception) for r in dict(resultados).values()):
            primeira = primeira or time.monotonic()
            if time.monotonic() - primeira > ctx.graca:
                break
        time.sleep(0.05)
    foto = dict(resultados)   # as threads lentas ainda podem gravar: decide-se tudo em cima desta cópia
    _verificar(cancelado)
    if any(isinstance(r, Cancelado) for r in foto.values()):
        raise Cancelado()
    obras, erros = [], {}
    for fonte in ctx.fontes:
        r = foto.get(fonte.id)
        if fonte.id not in foto:
            erros[fonte.nome] = f"demorou mais de {ctx.graca:.0f} s para responder"
        elif isinstance(r, Exception):
            erros[fonte.nome] = str(r)
        elif r:
            obras.extend(r)
    return obras, erros


def encontrar(titulo: str, ctx: Contexto, cancelado: Callable[[], bool]) -> list[Candidato]:
    """Candidatos compatíveis com `titulo`, do melhor para o pior. Só tenta variações de busca
    (erros de digitação) se o título completo não achar nada compatível. Se nada serve e alguma fonte
    falhou, levanta BuscaIncompleta: "não achei" só vale quando todas as fontes responderam."""
    vistos: set[tuple[str, str]] = set()
    avisadas: set[str] = set()
    sem_resposta: dict[str, str] = {}
    for consulta in variantes_busca(titulo):
        _verificar(cancelado)
        if consulta != titulo:
            ctx.log(f"Sem correspondência ainda; busca alternativa: “{consulta}”", None)
        ctx.log(f"Buscando “{consulta}” em {', '.join(f.nome for f in ctx.fontes)}...", None)
        obras, erros = _consultar_fontes(consulta, ctx, cancelado)
        for nome, erro in erros.items():
            sem_resposta.setdefault(nome, erro)
            if nome not in avisadas:
                avisadas.add(nome)
                ctx.log(f"Fonte indisponível ({nome}): {erro}", None)
        if len(erros) == len(ctx.fontes):
            raise ErroFontes("; ".join(f"{n}: {e}" for n, e in erros.items()))
        candidatos = []
        for pos, obra in enumerate(obras):
            if (obra.fonte, obra.id) in vistos:
                continue
            vistos.add((obra.fonte, obra.id))
            nota = pontuar(titulo, obra.titulo, obra.autor)
            if nota > 0:
                bonus = BONUS_IDIOMA if obra.idioma.lower() in ctx.idiomas else 0.0
                candidatos.append((-(nota + bonus), pos, Candidato(nota, obra)))
        ctx.log(f"{len(obras)} resultado(s), {len(candidatos)} compatível(is) com o título.", None)
        if candidatos:
            return [c for _, _, c in sorted(candidatos, key=lambda x: (x[0], x[1]))]
    if sem_resposta:
        raise BuscaIncompleta("; ".join(f"{n}: {e}" for n, e in sem_resposta.items()))
    return []


def _baixar_arquivo(obra: Obra, arq: Arquivo, titulo_final: str, ctx: Contexto,
                    cancelado: Callable[[], bool]) -> Resultado:
    pasta_parciais = os.path.join(ctx.pasta, PASTA_PARCIAIS)
    os.makedirs(pasta_parciais, exist_ok=True)
    chave = hashlib.sha1(arq.url.encode()).hexdigest()[:10]
    parcial = os.path.join(pasta_parciais, f"{obra.fonte}-{obra.id}-{chave}.part")
    ctx.http.baixar(arq.url, parcial, ctx.progresso, cancelado)
    pronto = parcial[:-len(".part")] + arq.ext
    os.replace(parcial, pronto)
    try:
        validar_arquivo(pronto)
        sha = sha256_arquivo(pronto)
        igual = ctx.biblioteca.por_hash(sha)
        if igual and os.path.isfile(os.path.join(ctx.pasta, igual.arquivo)):
            os.remove(pronto)
            ctx.log(f"O conteúdo já existe na biblioteca como “{igual.arquivo}”.", None)
            return Resultado(igual.titulo, igual.arquivo, True, igual.fonte, igual.licenca)
        tamanho = os.path.getsize(pronto)
        nome = publicar(pronto, ctx.pasta, titulo_final, lambda m: ctx.log(m, None))
    except BaseException:
        if os.path.exists(pronto):
            os.remove(pronto)
        raise
    try:
        ctx.biblioteca.registrar(titulo=obra.titulo, autor=obra.autor, fonte=obra.fonte, fonte_id=obra.id,
                                 arquivo=nome, sha256=sha, tamanho=tamanho, licenca=obra.licenca)
    except Exception as e:
        # arquivo e registro andam juntos: sem registro, desfaz a publicação (senão a próxima tentativa
        # baixa de novo e cria "Livro (2).pdf")
        try:
            os.remove(os.path.join(ctx.pasta, nome))
        except OSError as erro_remocao:
            ctx.log(f"  não consegui remover “{nome}” depois da falha no registro: {erro_remocao}", None)
        raise FalhaDownload(f"não consegui registrar na biblioteca ({type(e).__name__}); "
                            "o arquivo foi removido, tente de novo") from e
    for resto in (parcial, parcial + ".json"):
        if os.path.exists(resto):
            os.remove(resto)
    return Resultado(obra.titulo, nome, False, obra.fonte, obra.licenca)


def processar(titulo: str, ctx: Contexto, cancelado: Callable[[], bool] = lambda: False) -> Resultado:
    """Fluxo completo para UM título. Levanta LivroNaoEncontrado, DownloadFalhou, ErroFontes ou Cancelado."""
    os.makedirs(ctx.pasta, exist_ok=True)
    ctx.estado("Buscando")
    candidatos = encontrar(titulo, ctx, cancelado)
    if not candidatos:
        raise LivroNaoEncontrado(titulo)
    fontes = {f.id: f for f in ctx.fontes}
    falhas: list[tuple[str, str]] = []
    for nota, obra in candidatos[:MAX_CANDIDATOS]:
        _verificar(cancelado)
        with ctx.trava_da_obra(obra):
            existente = ctx.biblioteca.por_fonte(obra.fonte, obra.id)
            if existente and os.path.isfile(os.path.join(ctx.pasta, existente.arquivo)):
                ctx.log(f"Encontrado: {obra.titulo} (compatibilidade {nota:.0%}) - já está na biblioteca.", None)
                return Resultado(existente.titulo, existente.arquivo, True, obra.fonte, existente.licenca)
            autor = _autor_util(obra)
            ctx.log(f"Obra: {obra.titulo}{' — ' + autor if autor else ''} [{fontes[obra.fonte].nome}] "
                    f"(compatibilidade {nota:.0%}; {obra.licenca})", None)
            try:
                arquivos = fontes[obra.fonte].arquivos(obra, ctx.http, cancelado)
            except ErroRede as e:
                ctx.log(f"  não foi possível listar os arquivos: {e}", None)
                falhas.append((obra.titulo, str(e)))
                continue
            if not arquivos:
                ctx.log("  nenhum arquivo de livro disponível para esta obra.", None)
                falhas.append((obra.titulo, "nenhum arquivo de livro disponível"))
                continue
            titulo_final = f"{obra.titulo} - {autor}" if autor else obra.titulo
            for arq in arquivos[:MAX_ARQUIVOS]:
                _verificar(cancelado)
                ctx.estado("Baixando")
                ctx.log(f"Baixando {arq.ext} ...", None)
                try:
                    return _baixar_arquivo(obra, arq, titulo_final, ctx, cancelado)
                except TipoNaoPermitido as e:
                    ctx.log(f"  recusado: {e}", None)
                    falhas.append((obra.titulo, str(e)))
                except FalhaDownload as e:
                    ctx.log(f"  falhou: {e}", None)
                    falhas.append((obra.titulo, str(e)))
    raise DownloadFalhou(*falhas[0])


def limpar_parciais_antigos(pasta: str, dias: float = 7) -> None:
    """Apaga downloads parciais abandonados há mais de `dias` dias (os recentes servem para retomar)."""
    base = os.path.join(pasta, PASTA_PARCIAIS)
    if not os.path.isdir(base):
        return
    limite = time.time() - dias * 86400
    for n in os.listdir(base):
        caminho = os.path.join(base, n)
        try:
            if os.path.getmtime(caminho) < limite:
                os.remove(caminho) if os.path.isfile(caminho) else shutil.rmtree(caminho, ignore_errors=True)
        except OSError:
            pass
