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
    "upcoming": re.compile(r'"upcomingEvents"\s*:'),  # site próprio da MELI (JSON embutido)
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
            q4 = sorted(set(re.findall(r"(?:apiKey|api_key|Event\.svc|GetEventList|/feed/[A-Za-z]+\.svc)[^\s\"'<>,;]{0,80}", html, re.I)))[:8]
            if q4:
                print(f"     feed: {q4}")
            scripts = sorted(set(re.findall(r"<script[^>]+src=[\"']([^\"']+)", html, re.I)))[:12]
            print(f"     scripts: {scripts}")
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
            hrefs = sorted(set(re.findall(r"href=[\"']([^\"'#]+)", html, re.I)))
            print(f"     hrefs ({len(hrefs)}): {hrefs[:70]}")
            for m in list(re.finditer(r"earnings|quarter|results|3Q26|Q3 ?20|November|nov\.? \d", html, re.I))[:12]:
                print(f"     cru…{html[max(0, m.start() - 150): m.end() + 350]!r}")
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
    provisorio: bool = field(default=False, compare=False)  # "Provisional date" (MELI)


_MESES = {m: i + 1 for i, m in enumerate(["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"])}
_RE_TRI = re.compile(r"\b([1-4])\s*[TQ]\s*(?:20)?(\d{2})\b", re.I)
_RE_TRI_EN = re.compile(r"\bQ([1-4])\s*['’]?\s*(?:20)?(\d{2})\b", re.I)  # "Q3'26", "Q3 2026"
_RE_CALL = re.compile(r"\bcall\b|teleconfer|videoconfer|webcast|confer[êe]ncia|apresenta[çc][ãa]o|earnings call", re.I)
_RE_DIVULGACAO = re.compile(r"divulga|resultado|results|release|earnings", re.I)
_RE_IGNORAR = re.compile(r"sil[êe]ncio|assembleia|informe|formul[áa]rio|dividend|juros|jcp|proventos", re.I)
_RE_HORAS = re.compile(r"(\d{1,2})[:h](\d{2})\s*(?:-|às|a|até|–)\s*(\d{1,2})[:h](\d{2})")
# Links de inscrição/transmissão do call (não canais genéricos do YouTube nem imagens).
_RE_LINK_CALL = re.compile(
    r"https?://[^\s\"'<>]*(?:zoom\.us/(?:webinar|j/|w/|s/)|mzgroup\.com/[^\s\"'<>]*(?:event|webcast|webinar|regist)|webcast|choruscall|on24\.com|"
    r"tenmeetings|ten\.com\.br|teams\.microsoft\.com/l/meetup|webinar|streamyard|vimeo\.com/event|"
    r"youtube\.com/live/|riweb\.com\.br/[^\s\"'<>]*(?:evento|webcast))[^\s\"'<>]*",
    re.I,
)


def classificar(titulo: str) -> tuple[str, str] | None:
    """'Videoconferência de Resultados 3T26' -> ('call', '3Q26'); eventos fora de resultado -> None."""
    if _RE_IGNORAR.search(titulo):
        return None
    m = _RE_TRI.search(titulo) or _RE_TRI_EN.search(titulo)
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


_RE_PT = re.compile(r"portugu[êe]s|portuguese|\(PT\)|\bPT-BR\b", re.I)
# "em inglês"/"English" marcam o link do call em inglês; "tradução simultânea para o inglês" não.
_RE_EN = re.compile(r"(?<!para o )(?<!para )\b(?:em|in)\s+(?:ingl[êe]s|english)\b|\benglish\s+(?:call|version|webcast|conference)\b|\(EN\)", re.I)


def _idioma_do_link(html: str, pos: int) -> int:
    """+1 se o texto perto do link indica português, -1 se indica inglês (0: indefinido)."""
    antes = _texto(html[max(0, pos - 700): pos])[-260:]
    depois = _texto(html[pos: pos + 400])[:120]
    perto = f"{antes} {depois}"
    # O título mais próximo antes do link manda (ex.: "VIDEOCONFERÊNCIA EM INGLÊS" depois de "... EM PORTUGUÊS").
    ult_pt = max((m.end() for m in _RE_PT.finditer(antes)), default=-1)
    ult_en = max((m.end() for m in _RE_EN.finditer(antes)), default=-1)
    if ult_en > ult_pt:
        return -1
    if ult_pt > ult_en or _RE_PT.search(depois):
        return 1
    return -1 if _RE_EN.search(perto) else 0


def links_de_call(html: str) -> list[str]:
    """Links de inscrição/transmissão do call; o do call em português primeiro, o em inglês por último."""
    html = html or ""
    achados = {}
    for m in _RE_LINK_CALL.finditer(html):
        link = m.group(0).replace("&amp;", "&")
        if re.search(r"\.(png|jpe?g|svg|gif|css|js)(\?|$)", link, re.I) or link in achados:
            continue
        achados[link] = _idioma_do_link(html, m.start())
    return sorted(achados, key=lambda l: -achados[l])  # sorted é estável: empate mantém a ordem da página


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
    # "Calendário de Eventos" também aparece no menu: usa a primeira ocorrência com eventos.
    for inicio in re.finditer(r"Calend[áa]rio de Eventos", texto, re.I):
        bloco = texto[inicio.end(): inicio.end() + 3000]
        fim = re.search(r"Ver todos", bloco, re.I)
        eventos = _eventos_do_bloco(ticker, bloco[: fim.start()] if fim else bloco, hoje)
        if eventos:
            return eventos
    return []


def _eventos_do_bloco(ticker: str, bloco: str, hoje: date) -> list[EventoRI]:
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


_RE_HORA_ET = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?\s*\(?(?:U\.?\s?S\.?\s*)?(?:ET|EST|EDT|Eastern)\b", re.I)


def _hora_et(texto: str, dia: date, fuso: str) -> time | None:
    """"4:30 p.m. ET" no dia `dia` -> horário em `fuso` (Brasília por padrão)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    m = _RE_HORA_ET.search(texto or "")
    if not m:
        return None
    hora = int(m[1]) % 12 + (12 if m[3].lower() == "p" else 0)
    et = datetime(dia.year, dia.month, dia.day, hora, int(m[2] or 0), tzinfo=ZoneInfo("America/New_York"))
    return et.astimezone(ZoneInfo(fuso)).time()


def de_upcoming(ticker: str, html: str, fuso: str = "America/Sao_Paulo") -> list[EventoRI]:
    """Site de RI da MELI: a agenda vem como JSON na página /news-and-events:

        "upcomingEvents":{"rows":[{"cells":[{"columnKey":"date","text":"2026-11-04T08:00:00"},
          {"columnKey":"event","text":"Q3'26 Results - Provisional date"},{"columnKey":"details",...}]}]}
    O horário do campo date não diz o fuso: só vale o escrito no texto com "ET" (ex.: "4:30 p.m. ET").
    """
    import json

    m = re.search(r'"upcomingEvents"\s*:\s*', html)
    if not m:
        return []
    dados, _ = json.JSONDecoder().raw_decode(html, m.end())
    saida = []
    for linha in dados.get("rows") or []:
        celulas = {c.get("columnKey"): c for c in linha.get("cells") or []}
        titulo = (celulas.get("event") or {}).get("text") or ""
        detalhes = (celulas.get("details") or {}).get("text") or ""
        cls = classificar(titulo)
        data_txt = (celulas.get("date") or {}).get("text") or ""
        if not cls or not re.match(r"\d{4}-\d{2}-\d{2}", data_txt):
            continue
        dia = date.fromisoformat(data_txt[:10])
        provisorio = bool(re.search(r"provisional|tentative|estimated", f"{titulo} {detalhes}", re.I))
        inicio = None if provisorio else _hora_et(f"{titulo} {detalhes}", dia, fuso)
        achados = links_de_call(json.dumps(linha))
        saida.append(EventoRI(ticker, cls[0], cls[1], dia, inicio, None, achados[0] if achados else "", titulo, provisorio))
    return saida


_ORDINAIS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_DATA_EN = r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s*\d{4})"


def _data_en(txt: str) -> date:
    from datetime import datetime

    return datetime.strptime(re.sub(r"\s+", " ", txt.replace(",", ", ")).replace(" ,", ","), "%B %d, %Y").date()


def de_comunicado(ticker: str, texto: str, fuso: str = "America/Sao_Paulo") -> list[EventoRI]:
    """Comunicado "X to Report Second Quarter 2026 Results" (ex.: Sea):

        ... plans to announce its second quarter 2026 results before the U.S. market opens on
        August 11, 2026 ... Date and time: 7:30 AM U.S. Eastern Time on August 11, 2026
        Webcast link: https://events.q4inc.com/attendee/265654308
    """
    texto = re.sub(r"\s+", " ", texto or "")
    m = re.search(r"to Report (First|Second|Third|Fourth) Quarter (?:and Full Year )?(20\d\d) (?:Financial )?Results", texto, re.I)
    if not m:
        return []
    rotulo = f"{_ORDINAIS[m[1].lower()]}Q{m[2][2:]}"
    saida = []
    div = (re.search(r"results? (?:\w+ ){0,3}(?:before|after|prior to) the U\.?S\.? market (?:opens|closes|open|close)\s+on " + _DATA_EN, texto, re.I)
           # MELI: "intends to release financial results for its second fiscal quarter ending June 30, 2026, on August 5, 2026"
           or re.search(r"release (?:its )?(?:financial )?results [^.]{0,120}?,? on " + _DATA_EN, texto, re.I))
    if div:
        saida.append(EventoRI(ticker, "resultado", rotulo, _data_en(div[1]), titulo=m[0]))
    meses = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    call = re.search(r"Date and time:\s*(.{0,80}?)\bon " + _DATA_EN, texto, re.I)
    if call:
        dia, hora_txt = _data_en(call[2]), call[1]
    else:  # MELI: "conference call and audio webcast, on August 5, at 5:00 p.m. Eastern Time"
        call = re.search(rf"(?:conference call|webcast)[^.]{{0,80}}?\bon ({meses}\s+\d{{1,2}})(?:,\s*(\d{{4}}))?,? at (.{{0,40}}?(?:Eastern|ET)\b)", texto, re.I)
        dia = _data_en(f"{call[1]}, {call[2] or m[2]}") if call else None
        hora_txt = call[3] if call else ""
    if call:
        link = (re.search(r"(?:Webcast|Registration) link:\s*(https?://\S+)", texto, re.I)
                or re.search(r"webcast[^.]{0,120}?link at (https?://\S+)", texto, re.I))
        saida.append(EventoRI(ticker, "call", rotulo, dia, _hora_et(hora_txt, dia, fuso), None,
                              link[1].rstrip(".,;)") if link else "", f"Conference call {m[1]} Quarter {m[2]}"))
    return saida


def _ordem_comunicado(url: str) -> str:
    """Chave para achar o comunicado mais recente: data no nome ("2026.07.28 ...") ou o trimestre
    ("MELI_to_Report_Second_Quarter_2026_...")."""
    nome = url.replace("%20", " ").replace("_", " ")
    d = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", nome)
    if d:
        return f"{d[1]}{d[2]}{d[3]}"
    q = re.search(r"(First|Second|Third|Fourth) Quarter (?:and Full Year )?(\d{4})", nome, re.I)
    return f"{q[2]}{_ORDINAIS[q[1].lower()] * 3:02d}99" if q else ""


def comunicados_pdf(bruto: str) -> list[str]:
    """Links de PDF "to Report ... Results" no texto (HTML ou JSON), do mais recente ao mais antigo."""
    pdfs = [u.replace("\\u002F", "/").replace("\\/", "/") for u in re.findall(r"https?:[^\"\s<>]+?\.pdf", bruto)]
    alvos = [u for u in dict.fromkeys(pdfs) if re.search(r"to(?:%20|[ _+-])Report", u, re.I)]
    return sorted(alvos, key=_ordem_comunicado, reverse=True)


def eventos_de_comunicado(ticker: str, bruto: str, fuso: str = "America/Sao_Paulo") -> list[EventoRI]:
    """Abre o comunicado "to Report ... Results" mais recente citado em `bruto` e lê os eventos."""
    import io

    from pypdf import PdfReader

    for url in comunicados_pdf(bruto)[:1]:
        r = _get(url.replace(" ", "%20"))
        texto = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(r.content)).pages)
        return de_comunicado(ticker, texto, fuso)
    return []


def de_noticias_pdf(ticker: str, dados, fuso: str = "America/Sao_Paulo") -> list[EventoRI]:
    """API de notícias em JSON (ex.: sea.com/api/invest/news) com os PDFs dos comunicados."""
    import json

    return eventos_de_comunicado(ticker, json.dumps(dados), fuso)


_MESES_EXTENSO = {
    **{m: i + 1 for i, m in enumerate(["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                                        "agosto", "setembro", "outubro", "novembro", "dezembro"])},
    **{m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                        "august", "september", "october", "november", "december"])},
    "marco": 3,
}
_RE_HORA_TEXTO = re.compile(
    rf"(?:videoconfer[êe]ncia|teleconfer[êe]ncia|confer[êe]ncia|\bcall\b|webcast|conference call)[^.]{{0,120}}?"
    rf"(?:(?P<d1>\d{{1,2}})\s+de\s+(?P<m1>{'|'.join(_MESES_EXTENSO)})|(?P<d2>\d{{1,2}})/(?P<m2>\d{{1,2}})(?:/\d{{2,4}})?"
    rf"|(?P<m3>{'|'.join(_MESES_EXTENSO)})\s+(?P<d3>\d{{1,2}}))"
    rf"(?:,?\s+(?:de\s+)?\d{{4}})?[^\d]{{0,25}}?(?P<h>\d{{1,2}})\s*(?:h\s*(?P<min_h>\d{{2}})?|:(?P<min>\d{{2}}))"
    rf"(?:\s*(?P<ampm>[ap])\.?m\b)?",
    re.I,
)


def horarios_no_texto(texto: str) -> dict[tuple[int, int], time]:
    """Horário do call escrito por extenso na home, ex. "Videoconferência: 6 de novembro 10h (Brasil) /
    8h (US-ET)" -> {(11, 6): 10:00}. Vale o primeiro horário após a data (Brasília nos sites daqui)."""
    saida: dict[tuple[int, int], time] = {}
    for m in _RE_HORA_TEXTO.finditer(texto or ""):
        if m["d1"]:
            dia, mes = int(m["d1"]), _MESES_EXTENSO[m["m1"].lower()]
        elif m["d2"]:
            dia, mes = int(m["d2"]), int(m["m2"])
        else:
            dia, mes = int(m["d3"]), _MESES_EXTENSO[m["m3"].lower()]
        hora, minuto = int(m["h"]), int(m["min"] or m["min_h"] or 0)
        if m["ampm"] and m["ampm"].lower() == "p" and hora < 12:
            hora += 12
        if 1 <= mes <= 12 and hora < 24 and minuto < 60:
            saida.setdefault((mes, dia), time(hora, minuto))
    return saida


def imagens_de_popup(html: str) -> list[str]:
    """Imagens dentro de pop-up/modal com cara de agenda de resultados (ex.: C&A "Agenda_3T26_PT_3.png")."""
    achadas = []
    for m in re.finditer(r"<div[^>]*(?:popup|pop-up|modal|lightbox|banner)[^>]*>", html or "", re.I):
        trecho = html[m.start(): m.start() + 3000]
        for src in re.findall(r"<img[^>]+src=[\"']([^\"']+\.(?:png|jpe?g|webp))", trecho, re.I):
            if re.search(r"agenda|resultado|result|call|confer|webcast|[1-4]T\d\d|[1-4]Q\d\d|earnings", src, re.I):
                achadas.append(src.replace("&amp;", "&"))
    return list(dict.fromkeys(achadas))[:3]


def ocr(url: str) -> str:
    """Texto de uma imagem via tesseract (instalado no workflow). Sem tesseract, devolve ""."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("tesseract"):
        log.info("tesseract não instalado; imagem %s não lida", url)
        return ""
    try:
        conteudo = _get(url).content
        with tempfile.NamedTemporaryFile(suffix=".img") as f:
            f.write(conteudo)
            f.flush()
            r = subprocess.run(["tesseract", f.name, "-", "-l", "por+eng"], capture_output=True, text=True, timeout=60)
        return re.sub(r"\s+", " ", r.stdout)
    except Exception as e:
        log.info("OCR falhou em %s (%s)", url, type(e).__name__)
        return ""


def _em_ingles(r) -> bool:
    url, texto = getattr(r, "url", "") or "", getattr(r, "text", "") or ""
    return bool(re.search(r"/en(?:-us)?/?(?:$|[?#])|/en/", url) or re.search(r"<html[^>]+lang=[\"']en", texto[:3000], re.I))


def _pagina_pt(ticker: str, url: str) -> str:
    """Página do RI em português: se vier em inglês (redirecionamento ou idioma da página), tenta a
    versão em português (sem "/en/" ou com ?lang=pt-br) para pegar o link do call em português."""
    r = _get(url)
    if not _em_ingles(r):
        return r.text
    base = re.sub(r"/en(?:-us)?(?=/|$)", "", getattr(r, "url", "") or url, count=1)
    for alternativa in dict.fromkeys([base, f"{base}{'&' if '?' in base else '?'}lang=pt-br"]):
        try:
            r2 = _get(alternativa)
        except Exception:
            continue
        if not _em_ingles(r2):
            log.info("%s: RI %s veio em inglês; usando %s", ticker, url, alternativa)
            return r2.text
    log.warning("%s: RI %s só disponível em inglês; o link do call pode ser o do call em inglês", ticker, url)
    return r.text


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
            html = _pagina_pt(ticker, url)
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
    elif home and _PLATAFORMAS["upcoming"].search(home):
        # MELI: agenda (às vezes "provisional") + comunicado "to Report ... Results" (data, call e link).
        eventos = de_upcoming(ticker, home)
        try:
            do_comunicado = eventos_de_comunicado(ticker, home)
        except Exception as e:
            log.info("%s: comunicado do RI não lido (%s)", ticker, type(e).__name__)
            do_comunicado = []
        chaves = {(e.tipo, e.rotulo) for e in do_comunicado}
        eventos = do_comunicado + [e for e in eventos if (e.tipo, e.rotulo) not in chaves]
    elif home and home.lstrip()[:1] in "{[":  # API de notícias com PDFs (ex.: Sea)
        import json

        eventos = de_noticias_pdf(ticker, json.loads(home))
    elif home:
        raise ValueError("plataforma do site de RI não reconhecida")
    else:
        raise ConnectionError("site de RI inacessível")
    # Horário do call só no texto da home (ex.: Renner: "Videoconferência: 6 de novembro 10h";
    # SBFG: pop-up "VIDEOCONFERÊNCIA ... Terça-feira, 10 de Novembro de 2026 11h (Brasília)").
    horas = horarios_no_texto(_texto(home))
    if any(e.tipo == "call" and not e.inicio and e.dia >= hoje for e in eventos):
        # C&A: o pop-up é uma imagem ("Agenda_3T26_PT.png"); o texto vem por OCR.
        for img in imagens_de_popup(home):
            horas = {**horarios_no_texto(ocr(img)), **horas}
    for e in eventos:
        if e.tipo == "call" and not e.inicio and (e.dia.month, e.dia.day) in horas:
            e.inicio = horas[(e.dia.month, e.dia.day)]
    # Pop-up/destaque da home com o link de inscrição: vale para o call cuja data a home cita junto
    # do horário; sem essa referência, só para o próximo call (até 21 dias).
    futuros = sorted((e for e in eventos if e.tipo == "call" and e.dia >= hoje), key=lambda e: e.dia)
    citados = [e for e in futuros if (e.dia.month, e.dia.day) in horas]
    alvo = citados[0] if citados else next((e for e in futuros if e.dia <= hoje + timedelta(days=21)), None)
    links = links_de_call(home)
    if alvo and not alvo.link and links:
        alvo.link = links[0]
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
            f"{e.tipo} {e.rotulo} {e.dia:%d/%m}{' ' + e.inicio.strftime('%H:%M') if e.inicio else ''}"
            f"{' +link' if e.link else ''}{' (provisória)' if e.provisorio else ''}"
            for e in achados) or "nenhum evento de resultado")
        eventos += achados
    return eventos, falhas


def sondar(urls: list[str], padrao: str = r"to Report|Quarter 20\d\d|conference call|cdn\.sea\.com|investor/[1-4]Q20"
           r"|teleconfer|videoconfer|webcast|\b\d{1,2}h\d{0,2}\b|\b11:00|3T26") -> None:
    """Diagnóstico de URLs avulsas: PDF vira texto; HTML mostra PDFs, APIs, trechos e as URLs
    citadas no JavaScript da página (sites Next.js carregam o conteúdo por API)."""
    from urllib.parse import urljoin

    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
        except Exception as e:
            print(f"\n== {url}: FALHA {type(e).__name__}: {str(e)[:150]}")
            continue
        tipo = r.headers.get("content-type", "")
        print(f"\n== {url} -> HTTP {r.status_code} {tipo} {len(r.content)}b")
        if "pdf" in tipo or r.content[:4] == b"%PDF":
            import io
            from pypdf import PdfReader

            print("\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(r.content)).pages)[:5000])
            continue
        html = r.text
        if len(html) < 20000 or html.lstrip()[:1] in "{[":  # resposta curta/JSON: mostra o conteúdo
            print(f"   conteúdo: {html[:6000]}")
        pdfs = sorted(set(re.findall(r"[^\s\"'<>()]+\.pdf", html)))[:30]
        apis = sorted(set(re.findall(r"https?://[^\s\"'<>]*(?:api|json|graphql)[^\s\"'<>]*", html, re.I)))[:20]
        print(f"   pdfs: {pdfs}")
        print(f"   apis: {apis}")
        for m in list(re.finditer(padrao, html, re.I))[:10]:
            print(f"   …{html[max(0, m.start() - 200): m.end() + 300]!r}")
        for img in imagens_de_popup(html):
            print(f"   ocr {img}: {ocr(img)[:2000]!r}")
        fm = mz_id(html)
        if fm:  # site MZ: mostra também a agenda da API (campos completos)
            import json

            print(f"   mz: {fm}")
            try:
                print(f"   mz future: {json.dumps(mz_eventos(fm, 'future'), ensure_ascii=False)[:6000]}")
            except Exception as e:
                print(f"   mz future: FALHA {e}")
            print(f"   texto: {_texto(html)[:4000]}")
        for src in re.findall(r"<script[^>]+src=[\"']([^\"']*(?:page|app)[^\"']*)", html, re.I)[:6]:
            try:
                js = requests.get(urljoin(url, src), headers=HEADERS, timeout=60).text
            except Exception:
                continue
            citadas = sorted(set(re.findall(r"[\"'`](https?://[^\"'`\s]{6,200}|/(?:api|v\d)[^\"'`\s]{2,200})[\"'`]", js)))
            if citadas:
                print(f"   js {src}: {citadas[:40]}")
