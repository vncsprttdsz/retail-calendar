"""Correções por trimestre que prevalecem sobre a planilha da B3 (CVM e datas manuais)."""

from __future__ import annotations

import re

from .exterior import _parse_manual
from .modelo import Evento

_RE_ROTULO = re.compile(r"\b([1-4]Q\d\d)\b")


def rotulo(e: Evento) -> str | None:
    """'Resultado 3Q26 (estimado)' -> '3Q26'."""
    m = _RE_ROTULO.search(e.evento)
    return m[1] if m else None


def tipo(e: Evento) -> str:
    """'call' para 'Call 3Q26'; 'resultado' para o resto."""
    return "call" if e.evento.lower().startswith("call") else "resultado"


def chave(e: Evento) -> tuple[str, str, str | None]:
    return e.ticker, tipo(e), rotulo(e)


def substituir(eventos: list[Evento], correcoes: list[Evento]) -> list[Evento]:
    """Cada correção substitui o evento de mesmo ticker, tipo e trimestre (ou entra como novo)."""
    alvos = {chave(c) for c in correcoes}
    mantidos = [e for e in eventos if chave(e) not in alvos]
    return mantidos + correcoes


def manuais(empresas: list[dict]) -> list[Evento]:
    """`manual: { 3Q26: "2026-11-09" }` em cada empresa de cobertura.empresas."""
    saida = []
    for item in empresas or []:
        nomes = item.get("nomes") or []
        for q, valor in (item.get("manual") or {}).items():
            dia, hora = _parse_manual(valor)
            saida.append(Evento(str(item["ticker"]).upper(), nomes[0] if nomes else item["ticker"], f"Resultado {str(q).upper()}", dia, hora))
    return saida
