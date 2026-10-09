"""Plantão de Notícias da B3: documentos das companhias publicados no mesmo dia.

Os dados abertos da CVM (IPE) chegam com cerca de uma semana de atraso; aqui o
calendário reapresentado aparece assim que a companhia o entrega.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

URL = "https://sistemasweb.b3.com.br/PlantaoNoticias/Noticias/"
HEADERS = {"User-Agent": "Mozilla/5.0 (retail-calendar)", "Accept": "application/json, text/html"}
_RE_RAD = re.compile(r"https?://www\.rad\.cvm\.gov\.br/ENET/[A-Za-z]+\.aspx\?[^\s\"'<>]+", re.I)


@dataclass
class Noticia:
    id: str
    agencia: str
    data_hora: str  # "2026-10-08 18:32:10"
    titulo: str
    conteudo: str

    @property
    def raiz(self) -> str:
        """Código entre parênteses no título: 'MAGAZINE LUIZA S.A. (MGLU) - ...' -> 'MGLU'."""
        m = re.search(r"\(([A-Z0-9]{4})[A-Z0-9-]*\)", self.titulo)
        return m[1] if m else ""

    @property
    def url(self) -> str:
        return f"{URL}Detail?idNoticia={self.id}&agencia={self.agencia}&dataNoticia={self.data_hora.replace(' ', '%20')}"


def listar(inicio: date, fim: date) -> list[Noticia]:
    r = requests.get(
        URL + "ListarTitulosNoticias",
        params={"agencia": 18, "palavra": "", "dataInicial": inicio.isoformat(), "dataFinal": fim.isoformat()},
        headers=HEADERS,
        timeout=60,
    )
    r.raise_for_status()
    saida = []
    for item in r.json():
        n = item.get("NwsMsg", item)
        saida.append(Noticia(str(n.get("id")), str(n.get("IdAgencia", 18)), str(n.get("dateTime", "")),
                             str(n.get("headline", "")), str(n.get("content") or "")))
    return saida


def links_rad(noticia: Noticia) -> list[str]:
    """Links para o documento no RAD/CVM: no conteúdo da lista ou na página de detalhe."""
    achados = _RE_RAD.findall(noticia.conteudo)
    if not achados:
        r = requests.get(noticia.url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        achados = _RE_RAD.findall(r.text)
    return list(dict.fromkeys(l.replace("&amp;", "&") for l in achados))


def diagnostico(raizes: set[str], hoje: date, dias: int = 10) -> None:
    noticias = listar(hoje - timedelta(days=dias), hoje)
    print(f"\n== Plantão de Notícias B3: {len(noticias)} notícias em {dias} dias")
    if noticias:
        print(f"  exemplo bruto: {noticias[0]}")
    cobertura = [n for n in noticias if n.raiz in raizes]
    print(f"  da cobertura: {len(cobertura)}")
    for n in cobertura:
        print(f"  {n.data_hora} | {n.titulo}")
    for n in [n for n in cobertura if "calend" in n.titulo.lower()][:1]:
        print(f"\n== {n.titulo} ({n.data_hora})\n  conteúdo: {n.conteudo[:1500]!r}")
        try:
            r = requests.get(n.url, headers=HEADERS, timeout=60)
            print(f"  detalhe HTTP {r.status_code}; links RAD: {_RE_RAD.findall(r.text)}")
            # A página de detalhe carrega o texto por JavaScript: mostra scripts e chamadas.
            scripts = re.findall(r"<script[^>]*src=[\"']([^\"']+)", r.text)
            print(f"  scripts: {scripts}")
            for trecho in re.findall(r"(?:url|ajax|\$\.(?:get|post|getJSON))[^;]{0,300}", r.text, re.I)[:15]:
                print(f"  js: {trecho!r}")
            corpo = r.text[r.text.find("<body"):]
            print(f"  corpo (sem head, 4000): {re.sub(chr(10) + '|' + chr(13), ' ', corpo)[:4000]!r}")
            for src in [s for s in scripts if "plantao" in s.lower() or "noticia" in s.lower()][:3]:
                u = src if src.startswith("http") else "https://sistemasweb.b3.com.br" + (src if src.startswith("/") else "/PlantaoNoticias/" + src.lstrip("./"))
                js = requests.get(u, headers=HEADERS, timeout=60).text
                print(f"  -- {u}: chamadas: {re.findall(r'Noticias/[A-Za-z]+', js)}")
        except Exception as e:
            print(f"  falha no detalhe: {e}")
