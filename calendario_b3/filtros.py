from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .modelo import Evento, LinhaFonte, normalizar


@dataclass
class Empresa:
    ticker: str
    nomes: list[str]

    @property
    def raiz(self) -> str:
        return normalizar(self.ticker).replace(" ", "")[:4]


def carregar_cobertura(itens: list) -> list[Empresa]:
    saida = []
    for item in itens or []:
        if isinstance(item, str):
            item = {"ticker": item}
        saida.append(Empresa(ticker=str(item["ticker"]).upper().strip(), nomes=list(item.get("nomes") or [])))
    return saida


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
