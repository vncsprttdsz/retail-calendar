"""Histórico em JSON: mantém eventos passados que a B3 já tirou do arquivo."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
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


def mesclar(
    antigos: list[Evento],
    novos: list[Evento],
    hoje: date,
    tickers: set[str],
    agora: datetime | None = None,
    preservar: set[str] = frozenset(),
) -> list[Evento]:
    """Eventos atuais da fonte + eventos passados do histórico.

    Eventos futuros que sumiram da fonte são descartados (remarcados/cancelados);
    eventos de empresas que saíram da cobertura também. Tickers em `preservar`
    (fonte fora do ar nesta rodada) mantêm também os eventos futuros do histórico.
    """
    agora_iso = (agora or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    por_chave = {e.chave: e for e in antigos}
    resultado: dict[str, Evento] = {}
    for e in novos:
        anterior = por_chave.get(e.chave)
        e.visto_em = anterior.visto_em if anterior and anterior.visto_em else agora_iso
        resultado[e.chave] = e
    for e in antigos:
        if e.chave not in resultado and e.ticker in tickers and (e.data < hoje or e.ticker in preservar):
            resultado[e.chave] = e
    return sorted(resultado.values(), key=lambda e: (e.data, e.ticker))
