"""Histórico em JSON: mantém eventos passados que a B3 já tirou do arquivo."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path

from .modelo import Evento


def carregar(caminho: Path) -> list[Evento]:
    if not caminho.exists():
        return []
    return [Evento.de_json(d) for d in json.loads(caminho.read_text(encoding="utf-8"))]


def salvar(caminho: Path, eventos: list[Evento]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    dados = [e.para_json() for e in sorted(eventos, key=lambda e: (e.data, e.ticker, e.evento))]
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


FUSO = "America/Sao_Paulo"
# Evento sem horário (dia inteiro) conta como começado às 9h do dia: calls no Brasil não começam antes.
INICIO_DIA_INTEIRO = time(9)


def comecou(e: Evento, agora: datetime | None, fuso: str = FUSO) -> bool:
    """O evento já começou em `agora` (UTC)? Sem `agora`, nada começou (compatível com quem não informa)."""
    if agora is None:
        return False
    from zoneinfo import ZoneInfo
    local = agora.astimezone(ZoneInfo(fuso))
    inicio = datetime.combine(e.data, e.hora or INICIO_DIA_INTEIRO, ZoneInfo(fuso))
    return local >= inicio


def _congelar(e: Evento, anterior: Evento) -> None:
    """Evento que já começou: horário, fim e link ficam os já conhecidos; a fonte só completa o que faltava.
    Depois do call, o RI troca o Zoom pela gravação ou tira o horário, e isso não é mudança do evento."""
    if anterior.hora is not None:
        e.hora = anterior.hora
    extras = dict(e.extras)
    for campo in ("fim", "link"):
        if anterior.extras.get(campo):
            extras[campo] = anterior.extras[campo]
    e.extras = extras


def _herdar(e: Evento, anterior: Evento) -> None:
    """Horário (início e fim) e link que a fonte deixou de mostrar ficam os já conhecidos."""
    if e.hora is None and anterior.hora is not None:
        e.hora = anterior.hora
        if anterior.extras.get("fim") and not e.extras.get("fim"):
            e.extras = {**e.extras, "fim": anterior.extras["fim"]}
    if anterior.extras.get("link") and not e.extras.get("link"):
        e.extras = {**e.extras, "link": anterior.extras["link"]}


def mesclar(
    antigos: list[Evento],
    novos: list[Evento],
    hoje: date,
    tickers: set[str],
    agora: datetime | None = None,
    preservar: set = frozenset(),
) -> list[Evento]:
    """Eventos atuais da fonte + eventos passados do histórico.

    Eventos futuros que sumiram da fonte são descartados (remarcados/cancelados);
    eventos de empresas que saíram da cobertura também. Itens de `preservar` — ticker
    ou (ticker, tipo) — tiveram a fonte fora do ar nesta rodada e mantêm também os
    eventos futuros do histórico. Calls de hoje ficam (a agenda do RI tira o evento do dia).

    Mesmo evento (mesma data) que agora vem sem horário ou sem link mantém os do histórico: no dia
    do call a home tira o pop-up e a agenda volta a mostrar o call sem horário e sem link; isso não
    é mudança (senão o convite seria atualizado para dia inteiro e sem o link).

    Evento que já começou (data passada, ou hoje depois do horário; sem horário, depois das 9h) não muda
    mais: horário e link conhecidos ficam, mesmo que a fonte mostre outros (ex.: VULC3, gravação de 2023
    no lugar do Zoom logo depois do call de 11/11/2026, que atualizou o convite para dia inteiro).
    """
    from .ajustes import tipo
    agora_iso = (agora or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    momento = agora if agora is not None else None
    por_chave = {e.chave: e for e in antigos}
    resultado: dict[str, Evento] = {}
    for e in novos:
        anterior = por_chave.get(e.chave)
        e.visto_em = anterior.visto_em if anterior and anterior.visto_em else agora_iso
        if anterior:
            _herdar(e, anterior)
            if anterior.data < hoje or comecou(anterior, momento):
                _congelar(e, anterior)
        resultado[e.chave] = e
    for e in antigos:
        if e.chave in resultado or e.ticker not in tickers:
            continue
        passado = e.data < hoje or (e.data == hoje and tipo(e) == "call")
        if passado or e.ticker in preservar or (e.ticker, tipo(e)) in preservar:
            resultado[e.chave] = e
    return sorted(resultado.values(), key=lambda e: (e.data, e.ticker))
