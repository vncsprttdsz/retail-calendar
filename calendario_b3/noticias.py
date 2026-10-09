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
_RE_RAD = re.compile(r"https?://www\.rad\.cvm\.gov\.br/ENET\w*/[A-Za-z]+\.aspx\?[^\s\"'<>]+", re.I)


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


def calendarios(tickers: list[str], hoje: date, dias: int = 30):
    """Calendários de Eventos Corporativos da cobertura publicados no Plantão nos últimos dias."""
    from .cvm import DocCVM

    por_raiz = {t[:4]: t for t in tickers}
    docs = []
    for n in listar(hoje - timedelta(days=dias - 1), hoje):  # o Plantão aceita até 30 dias
        t = por_raiz.get(n.raiz)
        if not t or "calendario de eventos" not in n.titulo.lower():
            continue
        links = links_rad(n)
        if not links:
            log.warning("%s: notícia de calendário sem link para o documento: %s", t, n.url)
            continue
        docs.append(DocCVM(t, "", "Plantão B3", n.titulo, "", n.data_hora, "R" if "(R)" in n.titulo else "", links[0]))
    return docs


def diagnostico(raizes: set[str], hoje: date, dias: int = 30) -> None:
    from .cvm import datas_de_resultado, texto_pdf

    noticias = listar(hoje - timedelta(days=dias - 1), hoje)
    cal = [n for n in noticias if n.raiz in raizes and "calendario de eventos" in n.titulo.lower()]
    print(f"\n== Plantão de Notícias B3: {len(noticias)} notícias em {dias} dias; calendários da cobertura: {len(cal)}")
    for i, n in enumerate(cal):
        links = []
        try:
            links = links_rad(n)
            datas = datas_de_resultado(texto_pdf(links[0])) if links else {}
            lidas = ", ".join(f"{q} {d:%d/%m}" for q, d in sorted(datas.items())) or "nenhuma data lida"
        except Exception as e:
            lidas = f"falha: {e}"
        print(f"  {n.data_hora} | {n.titulo}\n      {links[:1]} -> {lidas}")
        if i == 0 and links:
            _investigar_link(links[0])


def _investigar_link(link: str) -> None:
    """Mostra o que o RAD devolve para o link do Plantão e testa variantes de download direto."""
    r = requests.get(link, headers=HEADERS, timeout=60)
    print(f"\n  -- {link}: HTTP {r.status_code}, {r.headers.get('Content-Type')}, {len(r.content)} bytes, final {r.url}")
    t = r.text
    for padrao in (r"<iframe[^>]*>", r"<frame[^>]*>", r"<form[^>]*>", r"<embed[^>]*>", r"<object[^>]*>",
                   r"<input[^>]*type=.hidden[^>]*>", r"window\.open\([^)]*\)", r"location[^;]{0,200}",
                   r"[A-Za-z]+\.aspx\?[^\"'<>\s]{0,200}"):
        achados = list(dict.fromkeys(re.findall(padrao, t, re.I)))[:8]
        if achados:
            print(f"     {padrao}: {achados}")
    m = re.search(r"ID=(\d+)", link)
    if not m:
        return
    pid = int(m[1])
    base = "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?Tela=ext&descTipo=IPE&CodigoInstituicao=1"
    variantes = [f"{base}&numProtocolo={pid}"] + [
        f"{base}&numProtocolo={pid}&numSequencia={pid - 475294}&numVersao={v}" for v in (1, 2, 3, 4, 5)
    ]
    for u in variantes:
        try:
            rv = requests.get(u, headers=HEADERS, timeout=60)
            print(f"     {u[len(base):]} -> HTTP {rv.status_code} {'PDF' if rv.content.startswith(b'%PDF') else 'não-PDF'} {len(rv.content)} bytes")
        except Exception as e:
            print(f"     {u[len(base):]} -> {e}")
