"""Baixe Livros - interface gráfica para baixar livros de https://www.baixelivros.com.br/

Digite o título, o Chrome (headless) busca no site e salva o livro na pasta
"Teste de Download", criada ao lado do executável se não existir.

Só são abertos os resultados cujo título é compatível com o digitado (a
comparação tolera erros de digitação e acentuação). O resultado final
(concluído / não encontrado) aparece apenas no Log, em verde ou vermelho.

Cada tentativa de download usa uma subpasta temporária própria, então um
download lento de um livro nunca é atribuído a outro, e só arquivos de tipos
permitidos (PDF, EPUB...) chegam à pasta final.
"""
from __future__ import annotations

import difflib
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import unicodedata
from typing import Callable, NamedTuple
from tkinter import messagebox, scrolledtext, ttk
from urllib.parse import quote_plus, urljoin, urlsplit

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

try:
    import winsound
except ImportError:  # não-Windows
    winsound = None

SITE_HOST = "www.baixelivros.com.br"
SITE = f"https://{SITE_HOST}"
PASTA_NOME = "Teste de Download"
TIMEOUT_DOWNLOAD = 180   # s, contados a partir do início do download
TIMEOUT_INICIO_LINK = 30  # s esperando o download começar após abrir o link do arquivo
TIMEOUT_INICIO_BOTAO = 15  # s esperando o download começar após clicar no botão
TIMEOUT_PAGINA = 45      # s de carregamento de página
MAX_PAGINAS = 2
MAX_VARIANTES = 8        # limite de buscas alternativas (erros de digitação)
MIN_NOTA = 0.70          # nota mínima para considerar um título compatível
CREATE_NO_WINDOW = 0x08000000
PREFIXO_TMP = ".baixando-"
STOPWORDS = {"de", "da", "do", "das", "dos", "e", "o", "a", "os", "as", "um", "uma", "em", "no", "na"}
# Tipos de arquivo aceitos como "livro"; só os de EXT_ABRIR são abertos pelo botão "Abrir livro".
EXT_LIVROS = (".pdf", ".epub", ".mobi", ".azw", ".azw3", ".doc", ".docx", ".txt", ".zip", ".rar")
EXT_ABRIR = (".pdf", ".epub", ".mobi", ".azw", ".azw3", ".doc", ".docx", ".txt")
RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
              *(f"LPT{i}" for i in range(1, 10))}
TRANSLIT = str.maketrans({"ø": "o", "œ": "oe", "æ": "ae", "ł": "l", "đ": "d", "ð": "d", "þ": "th"})


class LivroNaoEncontrado(Exception):
    """Nenhum resultado compatível com o título digitado."""


class DownloadFalhou(Exception):
    """O livro foi encontrado, mas nenhum download pôde ser concluído."""

    def __init__(self, nome: str, motivo: str):
        super().__init__(f"{nome}: {motivo}")
        self.nome, self.motivo = nome, motivo


class ErroSite(Exception):
    """A página do site não tem o formato esperado (mudou, bloqueio ou indisponível)."""


class Cancelado(Exception):
    """O usuário clicou em Parar busca."""


class FalhaDownload(Exception):
    """Falha ao baixar UM candidato (com o motivo em português)."""


class TipoNaoPermitido(FalhaDownload):
    """O site entregou um arquivo de tipo não permitido (ex.: .exe)."""


class Resultado(NamedTuple):
    nome: str
    arquivo: str
    ja_existia: bool


class Pagina(NamedTuple):
    resultados: list          # [(titulo, url, meta)]
    sem_resultados: bool      # o site avisou "Nenhum livro encontrado" (a lista são sugestões)
    sugestao: str | None      # "Você quis dizer: X?"
    tem_proxima: bool


class Candidato(NamedTuple):
    nota: float
    nome: str
    url: str


def verificar(cancelado: Callable[[], bool]) -> None:
    if cancelado():
        raise Cancelado()


def pasta_base() -> str:
    if getattr(sys, "frozen", False):  # rodando como .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# --- comparação de títulos ---------------------------------------------------
def tokens(texto: str) -> list[str]:
    """Minúsculas, sem acentos nem pontuação, separado em palavras."""
    decomposto = unicodedata.normalize("NFKD", texto.casefold().translate(TRANSLIT))
    saida: list[str] = []
    for c in decomposto:
        # tira o acento só de letras latinas (em "й" a marca faz parte da letra)
        if unicodedata.combining(c) and saida and unicodedata.name(saida[-1], "").startswith("LATIN"):
            continue
        saida.append(c)
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", "".join(saida)))


def _melhor(palavra: str, candidatas: list[str]) -> float:
    return max((difflib.SequenceMatcher(None, palavra, c).ratio() for c in candidatas), default=0.0)


def titulo_principal(titulo: str) -> str:
    """'Dom Casmurro – Machado de Assis' -> 'Dom Casmurro'."""
    return re.split(r"\s+[–—-]\s+", titulo, maxsplit=1)[0]


def pontuar(digitado: str, titulo_livro: str, contexto: str = "") -> float:
    """0 se o livro não confere; senão uma nota entre MIN_NOTA e 1.

    Cada palavra digitada (exceto artigos/preposições) precisa ter uma palavra
    parecida no título: palavras de até 4 letras e números precisam ser idênticas,
    as maiores toleram ~1 erro. Só palavras longas (5+ letras, não numéricas)
    podem casar com o `contexto` (ex.: autor); pelo menos metade das palavras
    digitadas deve estar no próprio título.
    """
    todas = tokens(digitado)
    q = [w for w in todas if w not in STOPWORDS] or todas
    alvo = tokens(titulo_livro)
    extra = tokens(contexto)
    if not q or not alvo:
        return 0.0
    soma, no_titulo = 0.0, 0
    for w in q:
        minimo = 1.0 if len(w) <= 4 else 0.8
        r_titulo = _melhor(w, alvo)
        r = r_titulo
        if len(w) >= 5 and not w.isdigit():
            r = max(r, _melhor(w, extra))
        if r < minimo:
            return 0.0
        if r_titulo >= minimo:
            no_titulo += 1
        soma += r
    if no_titulo < (len(q) + 1) // 2:
        return 0.0
    consulta = " ".join(todas)
    total = max(difflib.SequenceMatcher(None, consulta, " ".join(alvo)).ratio(),
                difflib.SequenceMatcher(None, consulta, " ".join(tokens(titulo_principal(titulo_livro)))).ratio())
    nota = 0.7 * soma / len(q) + 0.3 * total
    return nota if nota >= MIN_NOTA else 0.0


def url_permitida(url: str) -> bool:
    """Páginas de livro: só https no domínio do próprio site."""
    p = urlsplit(url or "")
    return p.scheme == "https" and (p.hostname or "").lower() == SITE_HOST


def url_arquivo_permitida(url: str) -> bool:
    """Link de arquivo: https e a extensão do CAMINHO (não da query) é de livro."""
    p = urlsplit(url or "")
    return p.scheme == "https" and bool(p.hostname) and p.path.lower().endswith(EXT_LIVROS)


def ordenar_candidatos(digitado: str, resultados: list, tentados: set) -> list[Candidato]:
    """Candidatos compatíveis, do mais parecido ao menos; empate mantém a ordem do site."""
    bons = []
    for pos, (nome, url, meta) in enumerate(resultados):
        if url in tentados or not url_permitida(url):
            continue
        nota = pontuar(digitado, nome, meta)
        if nota > 0:
            bons.append((-nota, pos, Candidato(nota, nome, url)))
    return [c for _, _, c in sorted(bons, key=lambda b: (b[0], b[1]))]


def variantes_busca(titulo: str):
    """Consultas a tentar: o título e, como o site só acha trechos exatos, prefixos e
    sufixos das palavras (um erro de digitação quebra a palavra em duas partes
    corretas), do trecho maior para o menor."""
    vistos: set[str] = set()

    def novo(c: str) -> bool:
        chave = " ".join(tokens(c)) or c.strip().casefold()
        if chave and chave not in vistos:
            vistos.add(chave)
            return True
        return False

    if novo(titulo):
        yield titulo
    palavras = sorted({w for w in tokens(titulo) if w not in STOPWORDS})
    longas = [w for w in palavras if len(w) >= 3] or palavras
    candidatas = []
    for w in longas:
        if len(w) < 5:
            candidatas.append((len(w), w))
            continue
        for n in range(len(w), 3, -1):
            candidatas.append((n, w[:n]))
            if n < len(w):
                candidatas.append((n, w[-n:]))
    candidatas.sort(key=lambda c: -c[0])  # estável: mais específicas primeiro
    enviadas = 0
    for _, c in candidatas:
        if enviadas >= MAX_VARIANTES:
            break
        if novo(c):
            enviadas += 1
            yield c


# --- arquivos ----------------------------------------------------------------
def nome_seguro(titulo: str, pasta: str = "", ext: str = "") -> str:
    """Nome de arquivo válido no Windows a partir do título (sem extensão)."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", titulo).replace("…", "")
    t = re.sub(r"\s+", " ", t)
    limite = max(20, min(120, 250 - len(pasta) - len(ext) - 12))
    t = t[:limite].strip(" .") or "livro"
    if t.split(".")[0].strip().upper() in RESERVADOS:
        t = "_" + t
    return t


def ja_existe(pasta: str, titulo: str) -> str | None:
    """Nome do arquivo de livro já salvo com este título (qualquer extensão de livro)."""
    base = nome_seguro(titulo, pasta, ".pdf").lower()
    for n in sorted(os.listdir(pasta)):
        raiz, ext = os.path.splitext(n)
        if raiz.lower() == base and ext.lower() in EXT_LIVROS and os.path.isfile(os.path.join(pasta, n)):
            return n
    return None


def validar_arquivo(caminho: str) -> None:
    ext = os.path.splitext(caminho)[1].lower()
    if ext not in EXT_LIVROS:
        raise TipoNaoPermitido(f"tipo de arquivo não permitido ({ext or 'sem extensão'})")
    if os.path.getsize(caminho) == 0:
        raise FalhaDownload("o arquivo baixado está vazio")
    if ext == ".pdf":
        with open(caminho, "rb") as f:
            if b"%PDF-" not in f.read(1024):
                raise FalhaDownload("o arquivo baixado não é um PDF válido")


def publicar(origem: str, pasta: str, titulo: str, log: Callable[[str], None]) -> str:
    """Move o arquivo baixado para a pasta final, com o título do livro como nome."""
    ext = os.path.splitext(origem)[1].lower()
    base = nome_seguro(titulo, pasta, ext)
    for n in range(1, 100):
        nome = base + ("" if n == 1 else f" ({n})") + ext
        destino = os.path.join(pasta, nome)
        if os.path.exists(destino):
            continue
        for _ in range(10):
            try:
                os.rename(origem, destino)  # falha (em vez de sobrescrever) se já existir
                return nome
            except FileExistsError:
                break
            except PermissionError:  # o Chrome ainda está liberando o arquivo
                time.sleep(0.3)
            except OSError as e:
                log(f"Aviso: não foi possível salvar como “{nome}” ({e}); mantendo o nome original.")
                original = nome_seguro(os.path.splitext(os.path.basename(origem))[0], pasta, ext) + ext
                os.rename(origem, os.path.join(pasta, original))
                return original
    raise OSError(f"não foi possível salvar “{base}{ext}” na pasta de destino")


def remover_trabalho(trabalho: str, log: Callable[[str], None]) -> None:
    """Apaga a pasta temporária (downloads parciais incluídos), tentando de novo se o Chrome ainda a segura."""
    for _ in range(8):
        shutil.rmtree(trabalho, ignore_errors=True)
        if not os.path.exists(trabalho):
            return
        time.sleep(0.5)
    log(f"Aviso: não foi possível remover a pasta temporária {trabalho}")


def varrer_temporarios(pasta: str, idade_max: float = 3600) -> None:
    """Remove pastas temporárias esquecidas por execuções anteriores que foram encerradas à força."""
    agora = time.time()
    for n in os.listdir(pasta):
        caminho = os.path.join(pasta, n)
        if n.startswith(PREFIXO_TMP) and os.path.isdir(caminho) and agora - os.path.getmtime(caminho) > idade_max:
            shutil.rmtree(caminho, ignore_errors=True)


# --- navegador ---------------------------------------------------------------
def criar_driver(pasta_download: str):
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1366,900")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--log-level=3")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    opts.add_experimental_option("prefs", {
        "download.default_directory": pasta_download,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,  # baixa PDF em vez de abrir
        "safebrowsing.enabled": True,
    })
    service = Service(log_output=os.devnull)
    service.creation_flags = CREATE_NO_WINDOW  # sem janela de console do driver
    driver = webdriver.Chrome(options=opts, service=service)
    driver.set_page_load_timeout(TIMEOUT_PAGINA)
    return driver


def matar_driver(driver) -> bool:
    """Encerra na hora o chromedriver e o Chrome (sem esperar comandos pendentes).

    Retorna True se o processo foi encerrado agora; False se já estava morto ou falhou."""
    try:
        proc = driver.service.process
        if proc is None or proc.poll() is not None:
            return False
        r = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def encerrar_driver(driver, cancelado: Callable[[], bool]) -> None:
    """Fecha o navegador sem nunca levantar exceção (não pode mascarar o resultado)."""
    if driver is None:
        return
    try:
        if cancelado():
            matar_driver(driver)
        else:
            try:
                driver.quit()
            except Exception:
                matar_driver(driver)
    except Exception:
        pass


def fechar_janelas_extras(driver) -> None:
    """Fecha abas/popups abertos por anúncios e volta para a aba principal."""
    try:
        handles = driver.window_handles
        for h in handles[1:]:
            driver.switch_to.window(h)
            driver.close()
        driver.switch_to.window(handles[0])
    except WebDriverException:
        pass


def aguardar_download(pasta: str, cancelado: Callable[[], bool], inicio_max: float = TIMEOUT_INICIO_LINK,
                      timeout: float = TIMEOUT_DOWNLOAD) -> str:
    """Espera um livro completo aparecer em `pasta` (subpasta exclusiva desta tentativa).

    Levanta FalhaDownload se nada começar em `inicio_max` s ou se, depois de começar,
    demorar mais de `timeout` s; TipoNaoPermitido se o arquivo não for de um tipo de livro."""
    inicio = time.monotonic()
    comecou = None
    while True:
        verificar(cancelado)
        agora = time.monotonic()
        nomes = os.listdir(pasta)
        parciais = [n for n in nomes if n.lower().endswith((".crdownload", ".tmp"))]
        completos = [n for n in nomes if n not in parciais and not n.lower().endswith((".htm", ".html"))]
        for n in completos:  # o Chrome grava um "downloads.htm" na pasta; o resto precisa ser livro
            ext = os.path.splitext(n)[1].lower()
            if ext not in EXT_LIVROS:
                raise TipoNaoPermitido(f"tipo de arquivo não permitido ({ext or 'sem extensão'})")
        if completos and not parciais:
            return max(completos, key=lambda n: os.path.getmtime(os.path.join(pasta, n)))
        if (parciais or completos) and comecou is None:
            comecou = agora
        if comecou is None and agora - inicio > inicio_max:
            raise FalhaDownload(f"o download não começou em {inicio_max:.0f} s")
        if comecou is not None and agora - comecou > timeout:
            raise FalhaDownload(f"o download demorou mais de {timeout:.0f} s")
        time.sleep(0.3)


def url_busca(titulo: str, pagina: int = 1) -> str:
    extra = f"&pg={pagina}" if pagina > 1 else ""
    return f"{SITE}/search?q={quote_plus(titulo)}{extra}"


def _texto(elemento) -> str:
    return " ".join((elemento.get_attribute("textContent") or "").split())


def buscar(driver, titulo: str, pagina: int = 1) -> Pagina:
    """Abre a página de resultados. Levanta ErroSite se ela não tiver o formato esperado,
    para nunca confundir um problema do site com "livro não encontrado"."""
    try:
        driver.get(url_busca(titulo, pagina))
    except TimeoutException:
        raise ErroSite("O site demorou demais para responder.")
    totais = driver.find_elements(By.CSS_SELECTOR, ".bl-bg-resultados__total")
    if not totais:
        raise ErroSite(f"A página de busca não tem o formato esperado (título: “{driver.title}”). "
                       "O site pode ter mudado, estar indisponível ou bloqueando o acesso.")
    sem_resultados = "nenhum livro" in _texto(totais[0]).casefold()
    resultados = []
    for item in driver.find_elements(By.CSS_SELECTOR, "li.bl-rs__item"):
        links = item.find_elements(By.CSS_SELECTOR, "h3 a")
        if not links:
            continue
        nome, href = _texto(links[0]), links[0].get_attribute("href") or ""
        meta = item.find_elements(By.CSS_SELECTOR, ".bl-rs__meta")
        if nome and href:
            resultados.append((nome, href, _texto(meta[0]) if meta else ""))
    achou = re.search(r"Você quis dizer:\s*(.+?)\?", _texto(totais[0].find_element(By.XPATH, "../..")))
    proxima = driver.find_elements(By.CSS_SELECTOR, "nav.bl-rs__paginacao a[rel='next']")
    return Pagina(resultados, sem_resultados, achou.group(1).strip() if achou else None, bool(proxima))


def baixar(driver, url: str, trabalho: str, cancelado: Callable[[], bool]) -> str:
    """Baixa o livro da página `url` numa subpasta exclusiva. Retorna o caminho do arquivo
    validado, ou levanta FalhaDownload com o motivo."""
    try:
        driver.get(url)
    except TimeoutException:
        raise FalhaDownload("a página do livro demorou demais para carregar")
    botoes = driver.find_elements(By.ID, "botaodownloadoriginal")
    if not botoes:
        raise FalhaDownload("a página do livro não tem botão de download")
    bruto = botoes[0].get_attribute("data-target") or botoes[0].get_attribute("href") or ""
    alvo = urljoin(url, bruto) if bruto else ""
    pasta = tempfile.mkdtemp(dir=trabalho)
    driver.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": pasta})

    def abrir_link():
        try:
            driver.get(alvo)
        except TimeoutException:  # navegar para um download pode não "terminar" a página
            pass

    def clicar():
        driver.execute_script("arguments[0].click();", botoes[0])

    tentativas = [(clicar, TIMEOUT_INICIO_BOTAO)]
    if url_arquivo_permitida(alvo):  # o link direto é mais rápido e confiável que o clique
        tentativas.insert(0, (abrir_link, TIMEOUT_INICIO_LINK))
    arquivo, falha = None, None
    try:
        for acionar, inicio_max in tentativas:
            verificar(cancelado)
            acionar()
            try:
                arquivo = aguardar_download(pasta, cancelado, inicio_max)
                break
            except TipoNaoPermitido:
                raise
            except FalhaDownload as e:
                falha = e
    finally:
        fechar_janelas_extras(driver)
    if arquivo is None:
        raise falha
    caminho = os.path.join(pasta, arquivo)
    validar_arquivo(caminho)
    return caminho


def _tentar_candidatos(driver, candidatos, tentados, falhas, pasta, trabalho, log, cancelado) -> Resultado | None:
    for nota, nome, url in candidatos:
        tentados.add(url)
        existente = ja_existe(pasta, nome)
        if existente:
            log(f"Encontrado: {nome} (compatibilidade {nota:.0%}) - já está na pasta.")
            return Resultado(nome, existente, True)
        log(f"Abrindo: {nome} (compatibilidade {nota:.0%})")
        try:
            caminho = baixar(driver, url, trabalho, cancelado)
        except FalhaDownload as e:
            log(f"  download não disponível: {e}")
            falhas.append((nome, str(e)))
            continue
        except WebDriverException as e:
            verificar(cancelado)  # erros causados pelo Parar
            motivo = f"erro do navegador ({type(e).__name__})"
            log(f"  {motivo}")
            falhas.append((nome, motivo))
            continue
        return Resultado(nome, publicar(caminho, pasta, nome, log), False)
    return None


def _buscar_e_baixar(driver, titulo, pasta, trabalho, log, cancelado) -> Resultado:
    tentados: set[str] = set()
    feitas: set[str] = set()
    falhas: list[tuple[str, str]] = []
    sugeridas: list[str] = []
    gerador = variantes_busca(titulo)
    while True:
        consulta = sugeridas.pop(0) if sugeridas else next(gerador, None)
        if consulta is None:
            break
        chave = " ".join(tokens(consulta)) or consulta.strip().casefold()
        if chave in feitas:
            continue
        feitas.add(chave)
        if consulta != titulo:
            log(f"Sem correspondência ainda; busca alternativa: “{consulta}”")
        for pagina in range(1, MAX_PAGINAS + 1):
            verificar(cancelado)
            log(f"Buscando na página {pagina}: {url_busca(consulta, pagina)}")
            pag = buscar(driver, consulta, pagina)
            verificar(cancelado)
            if pag.sugestao and pag.sugestao not in sugeridas:
                log(f"O site sugere: “{pag.sugestao}”")
                sugeridas.append(pag.sugestao)
            candidatos = ordenar_candidatos(titulo, pag.resultados, tentados)
            origem = "sugestão(ões) do site" if pag.sem_resultados else "resultado(s)"
            log(f"{len(pag.resultados)} {origem}, {len(candidatos)} compatível(is) com o título.")
            achado = _tentar_candidatos(driver, candidatos, tentados, falhas, pasta, trabalho, log, cancelado)
            if achado:
                return achado
            if not pag.tem_proxima:
                break
    if falhas:
        raise DownloadFalhou(*falhas[0])
    raise LivroNaoEncontrado(titulo)


def executar(titulo: str, pasta: str, log: Callable[[str], None], cancelado: Callable[[], bool],
             registrar_driver: Callable[[object], None]) -> Resultado:
    """Faz o fluxo completo e devolve o livro salvo em `pasta`."""
    os.makedirs(pasta, exist_ok=True)
    varrer_temporarios(pasta)
    trabalho = tempfile.mkdtemp(prefix=PREFIXO_TMP, dir=pasta)
    log(f"Pasta de destino: {pasta}")
    log("Abrindo navegador em segundo plano...")
    driver = None
    try:
        driver = criar_driver(trabalho)
        registrar_driver(driver)
        verificar(cancelado)
        return _buscar_e_baixar(driver, titulo, pasta, trabalho, log, cancelado)
    except (LivroNaoEncontrado, DownloadFalhou, ErroSite, Cancelado):
        raise
    except Exception:
        if cancelado():  # o navegador foi encerrado pelo botão Parar
            raise Cancelado()
        raise
    finally:
        encerrar_driver(driver, cancelado)
        remover_trabalho(trabalho, log)
        log("Navegador fechado.")


def dica_para(erro: BaseException) -> str:
    if isinstance(erro, ErroSite):
        return "O site pode ter mudado, estar fora do ar ou bloqueando o acesso automático."
    if isinstance(erro, WebDriverException):
        return "Verifique sua internet e se o Google Chrome está instalado."
    return "Veja os detalhes no Log."


class App(tk.Tk):
    PROMPT = "Digite o nome do livro e clique em Baixar."

    def __init__(self):
        super().__init__()
        self.title("Baixe Livros")
        self.geometry("580x560")
        self.resizable(False, False)
        self.pasta = os.path.join(pasta_base(), PASTA_NOME)
        self.fila: queue.Queue = queue.Queue()
        self.ultimo_arquivo: str | None = None
        self.driver = None
        self.thread: threading.Thread | None = None
        self.parar_evento = threading.Event()
        self._encerrando = False
        self.protocol("WM_DELETE_WINDOW", self.fechar_janela)

        estilo = ttk.Style(self)
        if "vista" in estilo.theme_names():
            estilo.theme_use("vista")
        estilo.configure("Titulo.TLabel", font=("Segoe UI", 18, "bold"))
        estilo.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#666666")
        estilo.configure("Erro.TLabel", font=("Segoe UI", 10, "bold"), foreground="#c62828")
        estilo.configure("Info.TLabel", font=("Segoe UI", 10))
        estilo.configure("Baixar.TButton", font=("Segoe UI", 10, "bold"), padding=6)

        corpo = ttk.Frame(self, padding=24)
        corpo.pack(fill="both", expand=True)

        ttk.Label(corpo, text="📚 Baixe Livros", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(corpo, text="baixelivros.com.br  •  os livros são salvos em “" + PASTA_NOME + "”",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 16))

        ttk.Label(corpo, text="Nome do livro", style="Info.TLabel").pack(anchor="w")
        linha = ttk.Frame(corpo)
        linha.pack(fill="x", pady=(4, 12))
        self.entrada = ttk.Entry(linha, font=("Segoe UI", 11))
        self.entrada.pack(side="left", fill="x", expand=True, ipady=4)
        self.entrada.bind("<Return>", lambda _e: self.iniciar())
        self.botao = ttk.Button(linha, text="Baixar", style="Baixar.TButton", command=self.iniciar)
        self.botao.pack(side="left", padx=(8, 0))
        self.botao_parar = ttk.Button(linha, text="Parar busca", style="Baixar.TButton",
                                      command=self.parar, state="disabled")
        self.botao_parar.pack(side="left", padx=(6, 0))

        self.barra = ttk.Progressbar(corpo, mode="indeterminate")
        self.barra.pack(fill="x", pady=(0, 10))

        self.msg = ttk.Label(corpo, text=self.PROMPT, style="Info.TLabel", wraplength=530, justify="left")
        self.msg.pack(anchor="w")

        self.acoes = ttk.Frame(corpo)
        self.acoes.pack(anchor="w", pady=(12, 0))
        ttk.Button(self.acoes, text="Abrir pasta", command=self.abrir_pasta).pack(side="left")
        self.botao_arquivo = ttk.Button(self.acoes, text="Abrir livro", command=self.abrir_arquivo)

        ttk.Label(corpo, text="Log", style="Info.TLabel").pack(anchor="w", pady=(14, 2))
        self.log = scrolledtext.ScrolledText(corpo, height=10, font=("Consolas", 9), state="disabled",
                                             background="#1e1e1e", foreground="#d4d4d4", relief="flat")
        self.log.tag_configure("ok", foreground="#3fb950", font=("Consolas", 9, "bold"))
        self.log.tag_configure("erro", foreground="#f85149", font=("Consolas", 9, "bold"))
        self.log.pack(fill="both", expand=True)

        self.entrada.focus_set()
        self.after(100, self.processar_fila)

    # --- ações -------------------------------------------------------------
    def escrever_log(self, texto: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {texto}\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def definir_msg(self, texto: str, estilo: str = "Info.TLabel") -> None:
        self.msg.configure(text=texto, style=estilo)

    def iniciar(self) -> None:
        if self.botao.instate(["disabled"]):
            return
        titulo = self.entrada.get().strip()
        if not titulo:
            self.definir_msg("Digite o nome do livro antes de baixar.", "Erro.TLabel")
            return
        self.botao.state(["disabled"])
        self.entrada.state(["disabled"])
        self.botao_parar.state(["!disabled"])
        self.botao_arquivo.pack_forget()
        self.barra.start(12)
        self.definir_msg("Iniciando...")
        self.escrever_log(f"--- Nova busca: “{titulo}” ---")
        self.driver = None
        self.parar_evento = threading.Event()
        self.thread = threading.Thread(target=self.trabalhar, args=(titulo, self.parar_evento), daemon=True)
        self.thread.start()

    def parar(self) -> None:
        if self.parar_evento.is_set():
            return
        self.parar_evento.set()
        self.botao_parar.state(["disabled"])
        self.definir_msg("Parando a busca...")
        driver = self.driver
        if driver:  # se ainda não existe, o worker encerra o navegador ao perceber o cancelamento
            threading.Thread(target=matar_driver, args=(driver,), daemon=True).start()

    def fechar_janela(self) -> None:
        self._encerrando = True  # a fila para de se reagendar
        self.parar_evento.set()
        self.withdraw()
        if self.driver:
            matar_driver(self.driver)
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=8)  # deixa o worker limpar o navegador e a pasta temporária
        self.destroy()

    def trabalhar(self, titulo: str, evento: threading.Event) -> None:
        def registrar(d):
            self.driver = d
        try:
            resultado = executar(titulo, self.pasta, lambda m: self.fila.put(("status", m)),
                                 evento.is_set, registrar)
            self.fila.put(("ok", resultado))
        except Cancelado:
            self.fila.put(("cancelado", titulo))
        except LivroNaoEncontrado:
            self.fila.put(("nao_encontrado", titulo))
        except DownloadFalhou as e:
            self.fila.put(("download_falhou", (e.nome, e.motivo)))
        except Exception as e:
            linhas = [ln for ln in str(e).splitlines() if ln.strip()]
            resumo = f"{type(e).__name__}: {linhas[0].strip() if linhas else '(sem mensagem)'}"
            self.fila.put(("erro", (resumo, traceback.format_exc(), dica_para(e))))

    def processar_fila(self) -> None:
        try:
            while True:
                try:
                    tipo, dado = self.fila.get_nowait()
                except queue.Empty:
                    break
                try:
                    if tipo == "status":
                        self.definir_msg(dado)
                        self.escrever_log(dado)
                    else:
                        self.finalizar(tipo, dado)
                except Exception:  # um erro ao exibir não pode parar a fila
                    try:
                        self.escrever_log("ERRO INTERNO:\n" + traceback.format_exc(), "erro")
                    except Exception:
                        pass
        finally:
            if not self._encerrando:
                self.after(100, self.processar_fila)

    def avisar(self, ok: bool) -> None:
        """Som + traz a janela para a frente (o resultado em si fica só no Log)."""
        if winsound:
            winsound.MessageBeep(winsound.MB_ICONASTERISK if ok else winsound.MB_ICONHAND)
        self.bell()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(500, lambda: self.attributes("-topmost", False))

    def finalizar(self, tipo: str, dado) -> None:
        self.barra.stop()
        self.botao.state(["!disabled"])
        self.botao_parar.state(["disabled"])
        self.entrada.state(["!disabled"])
        self.entrada.focus_set()
        self.definir_msg(self.PROMPT)
        self.driver = None
        self.thread = None

        if tipo == "cancelado":
            self.escrever_log("■ Busca interrompida.")
        elif tipo == "ok":
            nome, arquivo, ja_existia = dado
            self.ultimo_arquivo = os.path.join(self.pasta, arquivo)
            if ja_existia:
                self.escrever_log(f"✔ Concluído: “{nome}” já estava na pasta ({arquivo}); não foi baixado de novo.", "ok")
            else:
                self.escrever_log(f"✔ Concluído: “{nome}” salvo em {self.ultimo_arquivo}", "ok")
            if os.path.splitext(arquivo)[1].lower() in EXT_ABRIR:
                self.botao_arquivo.pack(side="left", padx=(8, 0))
            self.avisar(True)
        elif tipo == "nao_encontrado":
            self.escrever_log(f"✖ Livro não encontrado: “{dado}”", "erro")
            self.avisar(False)
        elif tipo == "download_falhou":
            nome, motivo = dado
            self.escrever_log(f"✖ Livro encontrado (“{nome}”), mas o download não foi possível: {motivo}", "erro")
            self.avisar(False)
        else:
            resumo, detalhe, dica = dado
            self.escrever_log(f"✖ Erro: {resumo}", "erro")
            self.escrever_log(detalhe.rstrip())
            self.avisar(False)
            messagebox.showerror("Erro", f"Não foi possível concluir a busca.\n\n{resumo}\n\n{dica}")

    def abrir_pasta(self) -> None:
        os.makedirs(self.pasta, exist_ok=True)
        os.startfile(self.pasta)

    def abrir_arquivo(self) -> None:
        caminho = self.ultimo_arquivo
        if caminho and os.path.isfile(caminho) and os.path.splitext(caminho)[1].lower() in EXT_ABRIR:
            os.startfile(caminho)


if __name__ == "__main__":
    App().mainloop()
