from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, time


def normalizar(texto: object) -> str:
    """Maiúsculas, sem acentos e com pontuação virando espaço."""
    if texto is None:
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^0-9A-Za-z]+", " ", s).upper()
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class LinhaFonte:
    """Uma linha lida da B3, antes de filtrar pela cobertura."""

    empresa: str
    codigo: str
    evento: str
    data: date
    hora: time | None = None
    periodo: str = ""


@dataclass
class Evento:
    ticker: str
    empresa: str
    evento: str
    data: date
    hora: time | None = None
    periodo: str = ""
    visto_em: str = ""  # ISO timestamp da primeira vez que o evento apareceu
    extras: dict = field(default_factory=dict)

    @property
    def chave(self) -> str:
        base = "|".join([self.ticker, normalizar(self.evento), normalizar(self.periodo), self.data.isoformat()])
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    def para_json(self) -> dict:
        d = asdict(self)
        d["data"] = self.data.isoformat()
        d["hora"] = self.hora.strftime("%H:%M") if self.hora else None
        return d

    @classmethod
    def de_json(cls, d: dict) -> "Evento":
        d = dict(d)
        d["data"] = date.fromisoformat(d["data"])
        d["hora"] = time.fromisoformat(d["hora"]) if d.get("hora") else None
        return cls(**d)
