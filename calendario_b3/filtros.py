from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from .modelo import Evento, LinhaFonte, normalizar


@dataclass
class Empresa:
    ticker: str
    nomes: list[str]

    @property
    def raiz(self) -> str:
        return normalizar(self.ticker).replace(" ", "")[:4]


def _de_lista(itens: list) -> list[Empresa]:
    saida = []
    for item in itens or []:
        if isinstance(item, str):
            item = {"ticker": item}
        saida.append(Empresa(ticker=str(item["ticker"]).upper().strip(), nomes=list(item.get("nomes") or [])))
    return saida


def carregar_cobertura(cfg: dict | list | None, base: Path = Path(".")) -> list[Empresa]:
    """Cobertura a partir de `arquivo` (config/coverage.yaml do retail-coverage), `empresas` (lista
    no próprio config) ou uma lista direta. `nomes_extras` acrescenta razões sociais por ticker."""
    if not isinstance(cfg, dict):
        return _de_lista(cfg or [])
    empresas: list[Empresa] = []
    caminho = os.environ.get("COVERAGE_FILE") or cfg.get("arquivo")
    if caminho:
        p = Path(caminho) if Path(caminho).is_absolute() else base / caminho
        if not p.exists():
            raise FileNotFoundError(f"arquivo de cobertura não encontrado: {p}")
        for c in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("companies") or []:
            if str(c.get("exchange", "B3")).upper() != "B3":
                continue  # ex.: MELI (NASDAQ) não está no cronograma da B3
            nome = str(c.get("name") or "")
            # Nomes curtos ("RD", "C&A") geram falso positivo na busca por nome; ficam só pelo ticker.
            nomes = [nome] if len(normalizar(nome).replace(" ", "")) >= 4 else []
            empresas.append(Empresa(str(c["ticker"]).upper().strip(), nomes))
    empresas += _de_lista(cfg.get("empresas") or [])

    extras = {str(k).upper(): list(v or []) for k, v in (cfg.get("nomes_extras") or {}).items()}
    por_ticker: dict[str, Empresa] = {}
    for e in empresas:
        atual = por_ticker.setdefault(e.ticker, Empresa(e.ticker, []))
        for n in [*e.nomes, *extras.get(e.ticker, [])]:
            if n not in atual.nomes:
                atual.nomes.append(n)
    return list(por_ticker.values())


def _contem_palavras(texto_norm: str, termo: str) -> bool:
    termo = normalizar(termo)
    return bool(termo) and re.search(rf"(?<![0-9A-Z]){re.escape(termo)}(?![0-9A-Z])", texto_norm) is not None


def empresa_da_linha(linha: LinhaFonte, cobertura: list[Empresa]) -> Empresa | None:
    codigos = {c[:4] for c in normalizar(linha.codigo).split() if len(c) >= 4}
    nome = normalizar(linha.empresa)
    for emp in cobertura:
        if emp.raiz in codigos:
            return emp
    for emp in cobertura:
        # Sem coluna de código: tenta ticker/raiz e nomes dentro do nome da empresa.
        if any(_contem_palavras(nome, t) for t in (emp.ticker, emp.raiz, *emp.nomes)):
            return emp
    return None


def eh_evento_de_resultado(linha: LinhaFonte, padroes: list[str]) -> bool:
    texto = normalizar(f"{linha.evento} {linha.periodo}")
    return any(re.search(normalizar_regex(p), texto) for p in padroes)


def normalizar_regex(padrao: str) -> str:
    # O texto é normalizado (sem acento, maiúsculo); o padrão segue a mesma regra,
    # preservando a sintaxe de regex.
    s = unicodedata.normalize("NFKD", padrao)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "(?i)" + s


def filtrar(linhas: list[LinhaFonte], cobertura: list[Empresa], padroes: list[str], incluir_todos: bool) -> list[Evento]:
    eventos: dict[str, Evento] = {}
    for linha in linhas:
        emp = empresa_da_linha(linha, cobertura)
        if emp is None:
            continue
        if not incluir_todos and not eh_evento_de_resultado(linha, padroes):
            continue
        ev = Evento(
            ticker=emp.ticker,
            empresa=linha.empresa or emp.ticker,
            evento=linha.evento,
            data=linha.data,
            hora=linha.hora,
            periodo=linha.periodo,
        )
        eventos.setdefault(ev.chave, ev)
    return sorted(eventos.values(), key=lambda e: (e.data, e.ticker))
