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


MZ_EVENTOS = "https://api.mziq.com/mzevents/events"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def mz_id(html: str) -> str | None:
    """Id da companhia na MZ: `fmId`, `COMPANIES[0].id` ou o usado nos links do file manager."""
    for padrao in (
        rf"fmId\s*=\s*[\"']({_UUID})",
        rf"COMPANIES\s*=\s*\[\s*\{{[^}}]*?[\"']?id[\"']?\s*:\s*[\"']({_UUID})",
        rf"mzfilemanager/v2/d/({_UUID})/",
    ):
        m = re.search(padrao, html, re.I)
        if m:
            return m[1]
    return None


def mz_eventos(fm_id: str, tipo: str = "future", lang: str = "pt-br"):
    r = requests.get(f"{MZ_EVENTOS}/{tipo}/{fm_id}/{lang}", headers=HEADERS, timeout=40)
    r.raise_for_status()
    return r.json()


def diagnostico_mz(empresas: list[dict]) -> None:
    """Para cada site MZ: id da companhia e o que a API de eventos devolve."""
    import json

    detalhados = 0
    for item in empresas:
        urls = item.get("ri") or []
        if not urls:
            continue
        try:
            r = requests.get(urls[0], headers=HEADERS, timeout=40)
            html = r.text
        except Exception as e:
            print(f"\n== MZ {item['ticker']}: site FALHA {type(e).__name__}")
            continue
        fm = mz_id(html)
        literal = re.findall(rf"(?:fmId|COMPANIES)[^;]{{0,200}}", html)[:2]
        print(f"\n== MZ {item['ticker']} fmId={fm} declarações={literal}")
        if not fm:
            continue
        for tipo in ("future", "calendar"):
            try:
                dados = mz_eventos(fm, tipo)
            except Exception as e:
                print(f"   {tipo}: FALHA {e}")
                continue
            texto = json.dumps(dados, ensure_ascii=False)
            print(f"   {tipo}: {type(dados).__name__} {len(texto)} chars")
            if detalhados < 3 or tipo == "future":
                print(f"   {texto[:2500 if detalhados < 3 else 900]}")
        detalhados += 1


# --------------------------------------------------------------------------- extração

from dataclasses import dataclass, field  # noqa: E402
from datetime import date, time, timedelta  # noqa: E402


@dataclass
class EventoRI:
    ticker: str
    tipo: str  # "resultado" | "call"
    rotulo: str  # "3Q26"
    dia: date
    inicio: time | None = None
    fim: time | None = None
    link: str = ""
    titulo: str = field(default="", compare=False)


_MESES = {m: i + 1 for i, m in enumerate(["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"])}
_RE_TRI = re.compile(r"\b([1-4])\s*[TQ]\s*(?:20)?(\d{2})\b", re.I)
_RE_CALL = re.compile(r"\bcall\b|teleconfer|videoconfer|webcast|confer[êe]ncia|apresenta[çc][ãa]o|earnings call", re.I)
_RE_DIVULGACAO = re.compile(r"divulga|resultado|release|earnings", re.I)
_RE_IGNORAR = re.compile(r"sil[êe]ncio|assembleia|informe|formul[áa]rio|dividend|juros|jcp|proventos", re.I)
_RE_HORAS = re.compile(r"(\d{1,2})[:h](\d{2})\s*(?:-|às|a|até|–)\s*(\d{1,2})[:h](\d{2})")
# Links de inscrição/transmissão do call (não canais genéricos do YouTube nem imagens).
_RE_LINK_CALL = re.compile(
    r"https?://[^\s\"'<>]*(?:zoom\.us/(?:webinar|j/|w/|s/)|mzgroup\.com|webcast|choruscall|on24\.com|"
    r"tenmeetings|ten\.com\.br|teams\.microsoft\.com/l/meetup|webinar|streamyard|vimeo\.com/event|"
    r"youtube\.com/live/|riweb\.com\.br/[^\s\"'<>]*(?:evento|webcast))[^\s\"'<>]*",
    re.I,
)


def classificar(titulo: str) -> tuple[str, str] | None:
    """'Videoconferência de Resultados 3T26' -> ('call', '3Q26'); eventos fora de resultado -> None."""
    if _RE_IGNORAR.search(titulo):
        return None
    m = _RE_TRI.search(titulo)
    if not m:
        return None
    rotulo = f"{m[1]}Q{m[2]}"
    if _RE_CALL.search(titulo):
        return "call", rotulo
    if _RE_DIVULGACAO.search(titulo):
        return "resultado", rotulo
    return None


def _hora(s: str | None) -> time | None:
    m = re.match(r"\s*(\d{1,2})[:h](\d{2})", s or "")
    return time(int(m[1]), int(m[2])) if m and int(m[1]) < 24 else None


def links_de_call(html: str) -> list[str]:
    achados = [l.replace("&amp;", "&") for l in _RE_LINK_CALL.findall(html or "")]
    return [l for l in dict.fromkeys(achados) if not re.search(r"\.(png|jpe?g|svg|gif|css|js)(\?|$)", l, re.I)]


def de_mz(ticker: str, eventos: list[dict]) -> list[EventoRI]:
    """Eventos da API mzevents (`/future/{fmId}/pt-br`)."""
    saida = []
    for ev in eventos or []:
        titulo = (ev.get("event_name") or "").strip()
        cls = classificar(titulo)
        if not cls or not ev.get("event_date"):
            continue
        link = (ev.get("external_link") or "").strip()
        if not link:
            achados = links_de_call(f"{ev.get('event_details') or ''} {ev.get('advanced') or ''}")
            link = achados[0] if achados else ""
        saida.append(EventoRI(
            ticker, cls[0], cls[1], date.fromisoformat(ev["event_date"][:10]),
            _hora(ev.get("event_starttime")), _hora(ev.get("event_endtime")), link, titulo,
        ))
    return saida


def de_riweb(ticker: str, html: str, hoje: date) -> list[EventoRI]:
    """Bloco "Calendário de Eventos" da home dos sites RIWeb (Magalu, RD):

        nov 09 Divulgação de Resultados 3T26 Após Fechamento do Mercado
        nov 10 Call Apresentação de Resultados 3T26 09:00 - 11:00 (Horário de Brasília)
    (ou "03 nov ..."). O ano não aparece: é o da próxima ocorrência da data.
    """
    texto = _texto(html)
    inicio = re.search(r"Calend[áa]rio de Eventos", texto, re.I)
    if not inicio:
        return []
    bloco = texto[inicio.end(): inicio.end() + 3000]
    fim = re.search(r"Ver todos", bloco, re.I)
    bloco = bloco[: fim.start()] if fim else bloco
    meses = "|".join(_MESES)
    marca = re.compile(rf"\b(?:(\d{{1,2}})\s+({meses})|({meses})\s+(\d{{1,2}}))\b", re.I)
    pos = list(marca.finditer(bloco))
    saida = []
    for i, m in enumerate(pos):
        titulo = bloco[m.end(): pos[i + 1].start() if i + 1 < len(pos) else len(bloco)].strip()
        cls = classificar(titulo)
        if not cls:
            continue
        dia_n, mes = (int(m[1]), m[2]) if m[1] else (int(m[4]), m[3])
        dia = date(hoje.year, _MESES[mes.lower()], dia_n)
        if dia < hoje - timedelta(days=60):
            dia = date(hoje.year + 1, dia.month, dia.day)
        horas = _RE_HORAS.search(titulo)
        inicio_h = time(int(horas[1]), int(horas[2])) if horas else None
        fim_h = time(int(horas[3]), int(horas[4])) if horas else None
        saida.append(EventoRI(ticker, cls[0], cls[1], dia, inicio_h, fim_h, "", titulo))
    return saida


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=40)
    r.raise_for_status()
    return r


def eventos_da_empresa(item: dict, hoje: date) -> list[EventoRI]:
    """Eventos de resultado e call no site de RI de uma empresa (MZ pela API; RIWeb pela home)."""
    ticker = str(item["ticker"]).upper()
    urls = item.get("ri") or []
    urls = [urls] if isinstance(urls, str) else list(urls)
    fm_id = item.get("mz")
    home = ""
    for url in urls:
        try:
            html = _get(url).text
        except Exception as e:
            log.info("%s: RI %s indisponível (%s)", ticker, url, type(e).__name__)
            continue
        home = home or html
        fm_id = fm_id or mz_id(html)
        if fm_id:
            break
    if fm_id:
        eventos = de_mz(ticker, mz_eventos(fm_id, "future"))
    elif home and _PLATAFORMAS["listaagenda"].search(home):
        eventos = de_riweb(ticker, home, hoje)
    elif home:
        raise ValueError("plataforma do site de RI não reconhecida")
    else:
        raise ConnectionError("site de RI inacessível")
    # Pop-up/destaque da home com o link de inscrição do call: vale para o próximo call (até 21 dias).
    calls = sorted((e for e in eventos if e.tipo == "call" and hoje <= e.dia <= hoje + timedelta(days=21)), key=lambda e: e.dia)
    links = links_de_call(home)
    if calls and not calls[0].link and links:
        calls[0].link = links[0]
    return eventos


def coletar(empresas: list[dict], hoje: date) -> tuple[list[EventoRI], set[str]]:
    """Eventos de RI de todas as empresas com `ri`/`mz` no config e os tickers que falharam."""
    eventos, falhas = [], set()
    for item in empresas or []:
        if not (item.get("ri") or item.get("mz")):
            continue
        ticker = str(item["ticker"]).upper()
        try:
            achados = eventos_da_empresa(item, hoje)
        except Exception as e:
            log.warning("%s: site de RI não lido (%s)", ticker, e)
            falhas.add(ticker)
            continue
        log.info("%s: RI -> %s", ticker, ", ".join(
            f"{e.tipo} {e.rotulo} {e.dia:%d/%m}{' ' + e.inicio.strftime('%H:%M') if e.inicio else ''}{' +link' if e.link else ''}"
            for e in achados) or "nenhum evento de resultado")
        eventos += achados
    return eventos, falhas
