"""Publica o .ics num Gist secreto (link estável para o Outlook assinar).

Variáveis de ambiente: GIST_ID e GIST_TOKEN (token com permissão "gist").
Uso: python -m calendario_b3.publicar publico/calendario.ics
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests


def publicar_gist(arquivo: Path, gist_id: str, token: str) -> str:
    r = requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"files": {arquivo.name: {"content": arquivo.read_text(encoding="utf-8")}}},
        timeout=60,
    )
    r.raise_for_status()
    dono = r.json()["owner"]["login"]
    return f"https://gist.githubusercontent.com/{dono}/{gist_id}/raw/{arquivo.name}"


def main() -> int:
    arquivo = Path(sys.argv[1] if len(sys.argv) > 1 else "publico/calendario.ics")
    gist_id, token = os.environ.get("GIST_ID"), os.environ.get("GIST_TOKEN")
    if not (gist_id and token):
        print("GIST_ID/GIST_TOKEN não configurados; nada a publicar.")
        return 0
    print("Publicado em:", publicar_gist(arquivo, gist_id, token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
