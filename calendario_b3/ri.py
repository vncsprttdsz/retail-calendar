"""Sites de Relações com Investidores: data de divulgação e teleconferência de resultados.

Camada de conferência: o site de RI costuma refletir uma remarcação antes da CVM e da B3,
e é onde aparece o horário e o link do webcast da teleconferência.
"""

from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

_PLATAFORMAS = {
    "mz": re.compile(r"mziq|mzgroup|mzweb|mz-ir|apicatalog", re.I),
    "listaagenda": re.compile(r"ListaAgenda|show\.aspx\?idCanal", re.I),
    "wordpress": re.compile(r"wp-content|wp-json", re.I),
    "q4": re.compile(r"q4cdn|q4inc|q4web", re.I),
}
_WEBCAST = re.compile(
    r"https?://[^\s\"'<>]*(?:zoom\.us|webcast|choruscall|on24|ten\.?meetings|mzgroup\.com/[^\s\"'<>]*event|"
    r"teams\.microsoft|youtube\.com|youtu\.be|vimeo|webinar|hiplatform|riweb|engage|events\.q4)[^\s\"'<>]*",
    re.I,
)
_CHAVES = re.compile(r"teleconfer|webcast|conference call|videoconfer|divulga[çc][ãa]o de resultados|3T26|3Q26", re.I)


def _texto(html: str) -> str:
    sem_script = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", sem_script))


def diagnostico(empresas: list[dict]) -> None:
    print("\n== Sites de RI")
    for item in empresas:
        urls = item.get("ri") or []
        if isinstance(urls, str):
            urls = [urls]
        for url in urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=40)
            except Exception as e:
                print(f"\n  {item['ticker']} {url}: FALHA {type(e).__name__}: {str(e)[:120]}")
                continue
            html = r.text
            plataformas = [n for n, p in _PLATAFORMAS.items() if p.search(html)]
            titulo = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            print(f"\n  {item['ticker']} {url} -> HTTP {r.status_code} {r.url} {len(html)}b "
                  f"plataforma={plataformas} título={(titulo[1].strip()[:60] if titulo else '')!r}")
            apis = sorted(set(re.findall(r"https?://[a-z0-9.-]*(?:api|mziq|apicatalog)[a-z0-9.-]*/[^\s\"'<>]{0,120}", html, re.I)))[:8]
            if apis:
                print(f"     apis: {apis}")
            webcasts = sorted(set(_WEBCAST.findall(html)))[:6]
            if webcasts:
                print(f"     webcast: {webcasts}")
            txt = _texto(html)
            vistos = 0
            for m in _CHAVES.finditer(txt):
                if vistos >= 5:
                    break
                vistos += 1
                print(f"     …{txt[max(0, m.start() - 120): m.end() + 160]}…")
            agenda = sorted(set(re.findall(r"href=[\"']([^\"']*(?:calend|agenda|evento|event)[^\"']*)", html, re.I)))[:8]
            if agenda:
                print(f"     links agenda: {agenda}")
            modal = re.findall(r"<div[^>]*(?:modal|popup|pop-up|lightbox)[^>]*>", html, re.I)[:3]
            if modal:
                print(f"     popup: {modal}")


def diagnostico_mz(urls: list[str]) -> None:
    """Como o site MZ chama a API de eventos (parâmetros, id da companhia)."""
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
        except Exception as e:
            print(f"\n== MZ {url}: FALHA {e}")
            continue
        html = r.text
        print(f"\n== MZ {url} (HTTP {r.status_code})")
        for m in list(re.finditer(r"mzevents", html))[:3]:
            print(f"   html: {html[max(0, m.start() - 700): m.end() + 900]!r}")
        for k in sorted(set(re.findall(r"(?:company[_-]?(?:id|uuid|key)|customer[_-]?key|apiKey|mzCompany|companyId|empresa_id)[\"']?\s*[:=]\s*[\"'][^\"']{4,60}", html, re.I)))[:10]:
            print(f"   chave: {k}")
        scripts = [u for u in re.findall(r"<script[^>]*src=[\"']([^\"']+)", html) if "themes" in u or "mz" in u.lower()][:12]
        for u in scripts:
            try:
                js = requests.get(requests.compat.urljoin(r.url, u), headers=HEADERS, timeout=40).text
            except Exception:
                continue
            for m in list(re.finditer(r"mzevents|events\?|/events", js))[:2]:
                print(f"   js {u.rsplit('/', 1)[-1][:40]}: {js[max(0, m.start() - 600): m.end() + 900]!r}")
