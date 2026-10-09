"""Avisos de mudança de data: WhatsApp (CallMeBot) e/ou Telegram.

Variáveis de ambiente (secrets do GitHub):
- CALLMEBOT_WHATSAPP: um "telefone:apikey" por pessoa, separados por vírgula ou linha,
  ex. "+5511999999999:123456, +5521988888888:654321". Cada pessoa ativa o próprio
  apikey no CallMeBot (ele só manda mensagem para o número que o autorizou).
- TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID (opcional).
Sem nenhum deles, as mudanças só aparecem no log.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date
from urllib.parse import quote_plus

import requests

from .ajustes import chave as _chave_evento
from .ics import titulo
from .modelo import Evento, normalizar

log = logging.getLogger(__name__)


def _quando(e: Evento) -> str:
    return f"{e.data:%d/%m}" + (f" {e.hora:%H:%M}" if e.hora else "")


def _chave(e: Evento) -> tuple:
    t, tp, q = _chave_evento(e)
    return t, tp, q or normalizar(e.evento)


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
            elif novo.extras.get("link") and novo.extras.get("link") != velho.extras.get("link"):
                linhas.append(f"🔗 {titulo(novo)} ({_quando(novo)}): {novo.extras['link']}")
        elif novo:
            linhas.append(f"🆕 {titulo(novo)}: {_quando(novo)}")
        else:
            linhas.append(f"🗑️ {titulo(velho)} ({_quando(velho)}) saiu do calendário")
    return linhas


CALLMEBOT = "https://api.callmebot.com/whatsapp.php"
_LIMITE_WHATSAPP = 1500  # caracteres por mensagem (o texto vai na URL)


def destinatarios_whatsapp(valor: str | None) -> list[tuple[str, str]]:
    """"+55 11 99999-9999:123456, ..." -> [("+5511999999999", "123456"), ...]."""
    saida = []
    for parte in re.split(r"[,;\n]+", valor or ""):
        if ":" not in parte:
            continue
        fone, chave = parte.rsplit(":", 1)
        fone = re.sub(r"[^\d+]", "", fone)
        if fone and chave.strip():
            saida.append((fone if fone.startswith("+") else "+" + fone, chave.strip()))
    return saida


def _blocos(texto: str, limite: int) -> list[str]:
    """Quebra por linha em mensagens de até `limite` caracteres."""
    blocos, atual = [], ""
    for linha in texto.split("\n"):
        if atual and len(atual) + 1 + len(linha) > limite:
            blocos.append(atual)
            atual = linha
        else:
            atual = f"{atual}\n{linha}" if atual else linha
    return blocos + ([atual] if atual else [])


def _whatsapp(texto: str, destinos: list[tuple[str, str]], pausa: float = 2.0) -> int:
    """Manda para cada pessoa; devolve quantas receberam. Telefone e apikey nunca vão para o log."""
    segredos = sorted({x for f, c in destinos for x in (c, quote_plus(c), quote_plus(f), f, f.lstrip("+"))}, key=len, reverse=True)
    ok = 0
    for i, (fone, chave) in enumerate(destinos, 1):
        try:
            for j, bloco in enumerate(_blocos(texto, _LIMITE_WHATSAPP)):
                if j or i > 1:
                    time.sleep(pausa)  # o CallMeBot limita mensagens seguidas
                r = requests.get(CALLMEBOT, params={"phone": fone, "text": bloco, "apikey": chave}, timeout=60)
                corpo = re.sub(r"<[^>]+>|\s+", " ", r.text or "").strip()
                if r.status_code != 200 or re.search(r"error|invalid|not\s+(?:allowed|activated)", corpo, re.I):
                    raise RuntimeError(f"HTTP {r.status_code}: {corpo[:200]}")
            ok += 1
        except Exception as e:
            detalhe = str(e)
            for segredo in segredos:
                detalhe = detalhe.replace(segredo, "***")
            log.error("falha ao enviar aviso no WhatsApp (destinatário %d): %s", i, detalhe)
    return ok


def _telegram(texto: str, token: str, chat: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto, "disable_web_page_preview": True},
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        # O token vai na URL: nunca deixar aparecer no log (o repositório é público).
        detalhe = e.response.text if e.response is not None else str(e)
        log.error("falha ao enviar aviso no Telegram: %s", detalhe.replace(token, "***"))
        return False
    return True


def enviar(linhas: list[str], pausa: float = 2.0) -> bool:
    """Avisa as mudanças nos canais configurados. Falha no aviso não derruba a atualização."""
    if not linhas:
        return False
    for l in linhas:
        log.info("mudança: %s", l)
    whatsapp = destinatarios_whatsapp(os.environ.get("CALLMEBOT_WHATSAPP"))
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not whatsapp and not (token and chat):
        log.info("CALLMEBOT_WHATSAPP (ou TELEGRAM_*) não configurado; aviso só no log")
        return False
    enviado = False
    if whatsapp:
        n = _whatsapp("*Calendário de resultados*\n\n" + "\n".join(linhas), whatsapp, pausa)
        log.info("aviso no WhatsApp enviado para %d de %d destinatário(s)", n, len(whatsapp))
        enviado = n > 0
    if token and chat:
        enviado = _telegram("Calendário de resultados\n\n" + "\n".join(linhas), token, chat) or enviado
    return enviado
