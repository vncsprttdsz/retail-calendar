"""Download da página da B3 e do arquivo do cronograma."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

_RE_ARQUIVO = re.compile(r"\.(xlsx|xls|csv)(\?|$)|fileDownload|/data/files/", re.I)
_RE_PDF = re.compile(r"\.pdf(\?|$)", re.I)


@dataclass
class Documento:
    url: str
    conteudo: bytes
    nome: str


def baixar(url: str, tentativas: int = 4) -> Documento:
    ultimo_erro: Exception | None = None
    for n in range(tentativas):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            r.raise_for_status()
            nome = _nome_arquivo(r) or url.rsplit("/", 1)[-1]
            return Documento(r.url, r.content, nome)
        except requests.RequestException as e:
            ultimo_erro = e
            espera = 2 ** (n + 1)
            log.warning("falha ao baixar %s (%s); nova tentativa em %ss", url, e, espera)
            time.sleep(espera)
    raise RuntimeError(f"não foi possível baixar {url}: {ultimo_erro}")


def _nome_arquivo(r: requests.Response) -> str:
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
    return m[1] if m else ""


def links_de_arquivo(html: str, base: str) -> list[str]:
    """Links para planilhas/CSV na página, priorizando os que citam o cronograma."""
    soup = BeautifulSoup(html, "lxml")
    candidatos: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not _RE_ARQUIVO.search(href) or _RE_PDF.search(href):
            continue
        texto = f"{a.get_text(' ', strip=True)} {a.get('title', '')} {href}".lower()
        prioridade = 0 if re.search(r"cronograma|evento", texto) else 1
        prioridade += 0 if re.search(r"\.xlsx?(\?|$)|xls", texto) else 1
        candidatos.append((prioridade, urljoin(base, href)))
    vistos, saida = set(), []
    for _, url in sorted(candidatos, key=lambda c: c[0]):
        if url not in vistos:
            vistos.add(url)
            saida.append(url)
    return saida


def iframes(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    return [urljoin(base, f["src"]) for f in soup.find_all("iframe", src=True)]
