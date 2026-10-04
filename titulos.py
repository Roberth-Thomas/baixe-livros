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
