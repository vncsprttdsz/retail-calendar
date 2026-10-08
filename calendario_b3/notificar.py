"""Avisos no Telegram quando uma data de resultado muda.

Variáveis de ambiente: TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID. Sem elas, as mudanças
só aparecem no log.
"""

from __future__ import annotations

import logging
import os
from datetime import date

import requests

from .ajustes import rotulo
from .ics import titulo
from .modelo import Evento, normalizar

log = logging.getLogger(__name__)


def _quando(e: Evento) -> str:
    return f"{e.data:%d/%m}" + (f" {e.hora:%H:%M}" if e.hora else "")


def _chave(e: Evento) -> tuple[str, str]:
    return e.ticker, rotulo(e) or normalizar(e.evento)


def diferencas(antes: list[Evento], depois: list[Evento], hoje: date) -> list[str]:
    """Mudanças em eventos de hoje em diante, uma linha por evento."""
    a = {_chave(e): e for e in antes if e.data >= hoje}
    d = {_chave(e): e for e in depois if e.data >= hoje}
    linhas = []
    for k in sorted(a.keys() | d.keys(), key=lambda k: (d.get(k) or a.get(k)).data):
        velho, novo = a.get(k), d.get(k)
        if velho and novo:
            if _quando(velho) != _quando(novo):
                linhas.append(f"📅 {titulo(novo)}: {_quando(velho)} → {_quando(novo)}")
            elif titulo(velho) != titulo(novo):
                linhas.append(f"✅ {titulo(novo)}: {_quando(novo)} (antes: {titulo(velho)})")
        elif novo:
            linhas.append(f"🆕 {titulo(novo)}: {_quando(novo)}")
        else:
            linhas.append(f"🗑️ {titulo(velho)} ({_quando(velho)}) saiu do calendário")
    return linhas


def enviar(linhas: list[str]) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not linhas:
        return False
    for l in linhas:
        log.info("mudança: %s", l)
    if not (token and chat):
        log.info("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID não configurados; aviso só no log")
        return False
    texto = "Calendário de resultados\n\n" + "\n".join(linhas)
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto, "disable_web_page_preview": True},
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        # O calendário já foi gravado; uma falha no aviso não deve derrubar a atualização.
        # O token vai na URL: nunca deixar aparecer no log (o repositório é público).
        detalhe = e.response.text if e.response is not None else str(e)
        log.error("falha ao enviar aviso no Telegram: %s", detalhe.replace(token, "***"))
        return False
    return True
