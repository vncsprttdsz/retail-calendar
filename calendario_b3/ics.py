"""Gera o arquivo iCalendar (.ics) assinado pelo Outlook."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .modelo import Evento

PRODID = "-//retail-calendar//Calendario de Resultados B3//PT"


def _escapar(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _dobrar(linha: str) -> str:
    """Quebra linhas em 75 octetos (RFC 5545 §3.1)."""
    b = linha.encode("utf-8")
    if len(b) <= 75:
        return linha
    partes, atual = [], b""
    for ch in linha:
        cb = ch.encode("utf-8")
        if len(atual) + len(cb) > (75 if not partes else 74):
            partes.append(atual.decode("utf-8"))
            atual = b""
        atual += cb
    partes.append(atual.decode("utf-8"))
    return "\r\n ".join(partes)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# Nome de exibição por ticker (`exibir` no config), no lugar do ticker: ex. {"CA": "Carrefour França"}.
NOMES: dict[str, str] = {}


def titulo(e: Evento) -> str:
    """'LREN Resultado 3Q26': ticker sem o número da classe (LREN3 -> LREN), ou o nome de exibição
    do config (ex.: 'Carrefour França Resultado 3Q26')."""
    evento = e.evento.strip() or "Evento corporativo"
    periodo = f" {e.periodo.strip()}" if e.periodo.strip() and e.periodo.strip() not in evento else ""
    ticker = NOMES.get(e.ticker) or re.sub(r"\d+$", "", e.ticker)
    return f"{ticker} {evento}{periodo}"


def uid(e: Evento) -> str:
    """Identificador do evento no calendário. Quando o evento ganha (ou muda de) horário, o UID muda:
    o Outlook, ao atualizar a assinatura, mescla um evento de mesmo UID com a versão antiga e mantém
    a duração de "dia inteiro" (24h). Com UID novo, ele apaga o antigo e cria o novo."""
    if e.hora is None:
        return e.chave
    return hashlib.sha1(f"{e.chave}|{e.hora:%H:%M}|{e.extras.get('fim', '')}".encode()).hexdigest()[:16]


def horario(e: Evento, tz: ZoneInfo, duracao_minutos: int) -> list[str]:
    """DTSTART/DTEND: dia inteiro sem horário; com horário, até `extras["fim"]` ou a duração padrão.
    Sempre como livre (não bloqueia a agenda)."""
    if e.hora is None:
        return [
            f"DTSTART;VALUE=DATE:{e.data:%Y%m%d}",
            f"DTEND;VALUE=DATE:{e.data + timedelta(days=1):%Y%m%d}",
            "TRANSP:TRANSPARENT",
            "X-MICROSOFT-CDO-BUSYSTATUS:FREE",
            "X-MICROSOFT-CDO-ALLDAYEVENT:TRUE",
        ]
    inicio = datetime.combine(e.data, e.hora, tzinfo=tz)
    fim = inicio + timedelta(minutes=duracao_minutos)
    if e.extras.get("fim"):
        h, m = map(int, e.extras["fim"].split(":"))
        fim = max(fim.replace(hour=h, minute=m), inicio + timedelta(minutes=15)) if (h, m) > (e.hora.hour, e.hora.minute) else fim
    return [f"DTSTART:{_utc(inicio)}", f"DTEND:{_utc(fim)}", "TRANSP:TRANSPARENT", "X-MICROSOFT-CDO-BUSYSTATUS:FREE"]


def conteudo(e: Evento) -> list[str]:
    link = e.extras.get("link")
    return [
        f"SUMMARY:{_escapar(titulo(e))}",
        *([f"DESCRIPTION:{_escapar('Webcast: ' + link)}", f"URL:{link}"] if link else []),
        "CATEGORIES:Resultados",
    ]


def gerar(eventos: list[Evento], nome: str, descricao: str, fuso: str, duracao_minutos: int) -> str:
    tz = ZoneInfo(fuso)
    linhas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escapar(nome)}",
        f"X-WR-CALDESC:{_escapar(descricao)}",
        f"X-WR-TIMEZONE:{fuso}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT4H",
        "X-PUBLISHED-TTL:PT4H",
    ]
    for e in eventos:
        visto = datetime.strptime(e.visto_em, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if e.visto_em else datetime(2000, 1, 1, tzinfo=timezone.utc)
        linhas += ["BEGIN:VEVENT", f"UID:{uid(e)}@retail-calendar", f"DTSTAMP:{_utc(visto)}"]
        linhas += [*horario(e, tz, duracao_minutos), *conteudo(e), "END:VEVENT"]
    linhas.append("END:VCALENDAR")
    return "\r\n".join(_dobrar(l) for l in linhas) + "\r\n"
