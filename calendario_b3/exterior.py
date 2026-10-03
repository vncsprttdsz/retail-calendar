"""Resultados de empresas listadas fora do Brasil (ex.: MELI na NASDAQ).

A B3 não publica essas datas. A próxima data vem do Yahoo Finance (via yfinance),
que informa se ela ainda é estimada. Datas manuais em config.yaml prevalecem:

    exterior:
      - { ticker: MELI, yahoo: MELI, nome: MercadoLibre, manual: { 3Q26: "2026-11-04 18:00" } }
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from .modelo import Evento

log = logging.getLogger(__name__)


@dataclass
class DataYahoo:
    dia: date
    hora: time | None  # já no fuso do calendário; None = dia inteiro
    estimado: bool


def trimestre_reportado(dia: date) -> str:
    """Trimestre cujo resultado sai nessa data: fev -> 4Q do ano anterior, mai -> 1Q, ago -> 2Q, nov -> 3Q."""
    if dia.month <= 3:
        return f"4Q{(dia.year - 1) % 100:02d}"
    return f"{(dia.month - 1) // 3}Q{dia.year % 100:02d}"


def _de_epoch(ts: int, tz: ZoneInfo) -> tuple[date, time | None]:
    # Data sem horário vem como meia-noite UTC; converter para o fuso local mudaria o dia.
    if ts % 86400 == 0:
        return datetime.fromtimestamp(ts, timezone.utc).date(), None
    local = datetime.fromtimestamp(ts, timezone.utc).astimezone(tz)
    return local.date(), local.time().replace(second=0, microsecond=0)


def interpretar_calendar_events(earnings: dict, tz: ZoneInfo) -> DataYahoo | None:
    """`earnings` do módulo calendarEvents do Yahoo (quoteSummary, formatted=false)."""
    datas = [_de_epoch(int(x), tz) for x in earnings.get("earningsDate") or []]
    if not datas:
        return None
    dias = sorted({d for d, _ in datas})
    # Intervalo de datas (ex.: 28/out–03/nov) ou flag do Yahoo = ainda não confirmado.
    estimado = bool(earnings.get("isEarningsDateEstimate")) or len(dias) > 1
    dia, hora = datas[0]
    return DataYahoo(dia, None if estimado else hora, estimado)


def consultar_yahoo(simbolo: str, tz: ZoneInfo) -> DataYahoo | None:
    import yfinance as yf

    ticker = yf.Ticker(simbolo)
    try:
        bruto = ticker._quote._fetch(modules=["calendarEvents"])  # traz isEarningsDateEstimate
        earnings = bruto["quoteSummary"]["result"][0]["calendarEvents"].get("earnings") or {}
        return interpretar_calendar_events(earnings, tz)
    except Exception as e:  # API interna do yfinance mudou: cai para a pública (sem a flag)
        log.warning("%s: calendarEvents bruto indisponível (%s); usando Ticker.calendar", simbolo, e)
    datas = (ticker.calendar or {}).get("Earnings Date") or []
    if not datas:
        return None
    return DataYahoo(datas[0], None, estimado=len(set(datas)) > 1)


def _parse_manual(valor: str) -> tuple[date, time | None]:
    m = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}):(\d{2}))?\s*", str(valor))
    if not m:
        raise ValueError(f"data manual inválida: {valor!r} (use AAAA-MM-DD ou AAAA-MM-DD HH:MM)")
    return date.fromisoformat(m[1]), (time(int(m[2]), int(m[3])) if m[2] else None)


def coletar(itens: list | None, fuso: str, consulta=None) -> tuple[list[Evento], set[str]]:
    """Eventos das empresas do exterior e o conjunto de tickers cuja consulta falhou
    (para esses, o histórico mantém os eventos futuros já conhecidos)."""
    consulta = consulta or consultar_yahoo
    tz = ZoneInfo(fuso)
    eventos: list[Evento] = []
    falhas: set[str] = set()
    for item in itens or []:
        ticker = str(item["ticker"]).upper()
        nome = item.get("nome") or ticker
        manuais = {str(q).upper(): _parse_manual(v) for q, v in (item.get("manual") or {}).items()}
        for q, (dia, hora) in manuais.items():
            eventos.append(Evento(ticker, nome, f"Resultado {q}", dia, hora))
        try:
            yahoo = consulta(item.get("yahoo") or ticker, tz)
        except Exception as e:
            log.warning("%s: falha ao consultar o Yahoo (%s); mantendo datas já conhecidas", ticker, e)
            falhas.add(ticker)
            continue
        if yahoo is None:
            log.info("%s: Yahoo sem próxima data de resultado", ticker)
            continue
        q = trimestre_reportado(yahoo.dia)
        log.info("%s: Yahoo -> %s %s %s (%s)", ticker, q, yahoo.dia, yahoo.hora or "", "estimado" if yahoo.estimado else "confirmado")
        if q in manuais:
            continue  # data manual prevalece
        titulo = f"Resultado {q}" + (" (estimado)" if yahoo.estimado else "")
        eventos.append(Evento(ticker, nome, titulo, yahoo.dia, yahoo.hora))
    return eventos, falhas
