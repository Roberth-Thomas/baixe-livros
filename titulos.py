"""Comparação de títulos digitados com os títulos encontrados (tolerante a erros de digitação)."""
from __future__ import annotations

import difflib
import re
import unicodedata

MIN_NOTA = 0.70          # nota mínima para considerar um título compatível
MAX_VARIANTES = 8        # limite de buscas alternativas (erros de digitação)
STOPWORDS = {"de", "da", "do", "das", "dos", "e", "o", "a", "os", "as", "um", "uma", "em", "no", "na"}
TRANSLIT = str.maketrans({"ø": "o", "œ": "oe", "æ": "ae", "ł": "l", "đ": "d", "ð": "d", "þ": "th"})


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


# --- "só livros": títulos com cara de jornal, revista, boletim ou documento oficial ---------------------
_ARTIGOS = {"o", "os", "as", "the", "el", "la", "los", "las", "le", "les"}   # "a" fica de fora: em inglês é "A Journal of..."
_FORTES = {"newsletter", "newspaper", "newspapers", "magazine", "magazines", "yearbook", "gazette", "gazeta",
           "bulletin", "boletim", "boletin", "almanac", "almanah", "almanaque", "periodico", "periodicos"}
_FRACAS = {"jornal", "journal", "revista", "revue", "rivista", "zeitschrift", "manual"}   # só se abrem o título
_CATEGORIAS = _FRACAS | {"jornais", "revistas", "journals", "manuais", "manuals"} | _FORTES   # busca "só a categoria"
_INSTITUCIONAIS = {"secretariat", "ministry", "ministerio", "government", "governo", "gobierno", "parliament",
                   "parlamento", "committee", "comite", "commission", "department", "departamento",
                   "jornal", "jornais", "newspaper", "gazette", "gazeta"}   # autor que é órgão/jornal, não pessoa
_MESES = {"jan", "janeiro", "january", "enero", "fev", "fevereiro", "feb", "february", "febrero", "marco", "march",
          "marzo", "abr", "abril", "apr", "april", "mai", "maio", "mayo", "jun", "junho", "june", "junio", "jul",
          "julho", "july", "julio", "ago", "agosto", "aug", "august", "sep", "sept", "setembro", "september",
          "septiembre", "oct", "outubro", "october", "octubre", "nov", "novembro", "november", "noviembre", "dec",
          "dezembro", "december", "diciembre", "dic"}
_MESES_AMBIGUOS = {"mar", "may", "set", "out", "dez"}   # também são palavras comuns: só contam com dia + ano
_ANO = re.compile(r"(1[5-9]|20)\d\d")
_DATA_OU_EDICAO = [re.compile(p) for p in (
    r"\b(1[5-9]|20)\d\d[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b",     # 1984-09-14
    r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](1[5-9]|20)\d\d\b",     # 25/04/1984
    r"\bn[º°]\s*\d+", r"\bnr\.?\s*\d+", r"\bn\.\s*\d+", r",\s*no\.?\s*\d+",       # Nº 22 · Nr. 14 · n.14 · , No. 40
    r"\bv\d+\s*n\d+", r"\bvol\.?\s*\d+\s*,?\s*(no|n)\.?\s*\d+",                    # v3n1 · Vol. 3, No. 2
    r"\banno?\s+([ivxlc]+|\d{1,2})\b", r"\bissue\s+\d+")]                        # Ano I · Anno 1 · Issue 4


def _tem_mes_e_ano(palavras: list[str]) -> bool:
    for i, p in enumerate(palavras):
        janela = palavras[i + 1:i + 4]
        if p in _MESES and any(_ANO.fullmatch(w) for w in janela):
            return True
        if p in _MESES_AMBIGUOS and len(janela) >= 2 and janela[0].isdigit() and len(janela[0]) <= 2 \
                and _ANO.fullmatch(janela[1]):
            return True
    return False


def eh_categoria(digitado: str) -> bool:
    """True se o que foi digitado é só um tipo de publicação ("jornal", "revistas"), não o título de um livro."""
    pedido = [p for p in tokens(digitado) if p not in STOPWORDS]
    return bool(pedido) and all(p in _CATEGORIAS for p in pedido)


def parece_periodico(titulo: str, autor: str = "", digitado: str = "") -> bool:
    """True se o título (ou o autor) tem cara de jornal, revista, boletim ou documento oficial — não de livro.

    Palavras que o próprio usuário digitou não contam contra o resultado quando o título digitado tem 2+
    palavras: quem busca "Jornal de um rebelde" quer esse título; quem digita só "jornal" pede uma categoria,
    e isso não é livro. Um ano sozinho ("Dom Casmurro (1899)") é normal em livro; data completa, mês + ano e
    número de edição não são."""
    pedido = [p for p in tokens(digitado) if p not in STOPWORDS]
    digitados = set(pedido) if len(pedido) >= 2 else set()
    if any(p.search(titulo.casefold()) for p in _DATA_OU_EDICAO):
        return True
    palavras = tokens(titulo)
    if _tem_mes_e_ano(palavras):
        return True
    if any(p in _FORTES and p not in digitados for p in palavras):
        return True
    if eh_categoria(digitado):
        return True   # quem digita só "jornal" ou "revista" pede uma categoria, e não existe livro com esse título
    abertura = [p for p in palavras if p not in _ARTIGOS][:1]
    if abertura and abertura[0] in _FRACAS and abertura[0] not in digitados:
        return True
    if any(p in _INSTITUCIONAIS and p not in digitados for p in tokens(autor)):
        return True
    principal = [p for p in tokens(titulo_principal(titulo)) if p not in STOPWORDS]
    if pedido and all(p.isdigit() for p in pedido) and len(principal) > 4:
        return True   # busca só por número ("1984"): qualquer item com o ano entra; só um título curto é o livro
    return len(principal) >= 10 and len(principal) >= 5 * max(len(pedido), 1)   # título enorme para uma palavra só


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
