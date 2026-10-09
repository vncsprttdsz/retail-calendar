"""Gera o arquivo iCalendar (.ics) assinado pelo Outlook."""

from __future__ import annotations

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


def titulo(e: Evento) -> str:
    """'LREN Resultado 3Q26': ticker sem o número da classe (LREN3 -> LREN)."""
    evento = e.evento.strip() or "Evento corporativo"
    periodo = f" {e.periodo.strip()}" if e.periodo.strip() and e.periodo.strip() not in evento else ""
    ticker = re.sub(r"\d+$", "", e.ticker)
    return f"{ticker} {evento}{periodo}"


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
        linhas += ["BEGIN:VEVENT", f"UID:{e.chave}@retail-calendar", f"DTSTAMP:{_utc(visto)}"]
        if e.hora is None:
            linhas += [
                f"DTSTART;VALUE=DATE:{e.data:%Y%m%d}",
                f"DTEND;VALUE=DATE:{e.data + timedelta(days=1):%Y%m%d}",
                "TRANSP:TRANSPARENT",
                "X-MICROSOFT-CDO-BUSYSTATUS:FREE",
                "X-MICROSOFT-CDO-ALLDAYEVENT:TRUE",
            ]
        else:
            inicio = datetime.combine(e.data, e.hora, tzinfo=tz)
            fim = inicio + timedelta(minutes=duracao_minutos)
            if e.extras.get("fim"):
                h, m = map(int, e.extras["fim"].split(":"))
                fim = max(fim.replace(hour=h, minute=m), inicio + timedelta(minutes=15)) if (h, m) > (e.hora.hour, e.hora.minute) else fim
            linhas += [
                f"DTSTART:{_utc(inicio)}",
                f"DTEND:{_utc(fim)}",
                "TRANSP:TRANSPARENT",
                "X-MICROSOFT-CDO-BUSYSTATUS:FREE",
            ]
        linhas += [
            f"SUMMARY:{_escapar(titulo(e))}",
            *([f"DESCRIPTION:{_escapar('Webcast: ' + e.extras['link'])}", f"URL:{e.extras['link']}"] if e.extras.get("link") else []),
            "CATEGORIES:Resultados",
            "END:VEVENT",
        ]
    linhas.append("END:VCALENDAR")
    return "\r\n".join(_dobrar(l) for l in linhas) + "\r\n"
