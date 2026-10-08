"""Calendário de Eventos Corporativos entregue pela companhia à CVM (dados abertos, IPE).

A planilha consolidada da B3 é atualizada de tempos em tempos; quando a companhia
reapresenta o calendário na CVM (ex.: remarca a divulgação do 3T), a data nova
aparece aqui antes.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date

import pandas as pd
import requests

log = logging.getLogger(__name__)

IPE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (retail-calendar)"}


@dataclass
class DocCVM:
    ticker: str
    cnpj: str
    categoria: str
    assunto: str
    data_referencia: str
    data_entrega: str
    versao: str
    link: str


def _so_digitos(cnpj: str) -> str:
    return re.sub(r"\D", "", str(cnpj))


def ler_ipe(anos: list[int]) -> pd.DataFrame:
    frames = []
    for ano in anos:
        r = requests.get(IPE_URL.format(ano=ano), headers=HEADERS, timeout=120)
        if r.status_code != 200:
            log.warning("IPE %s indisponível (HTTP %s)", ano, r.status_code)
            continue
        z = zipfile.ZipFile(io.BytesIO(r.content))
        nome = next(n for n in z.namelist() if n.endswith(".csv"))
        frames.append(pd.read_csv(z.open(nome), sep=";", encoding="latin1", dtype=str))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def documentos(ipe: pd.DataFrame, cnpjs: dict[str, str], categoria: str | None = None) -> list[DocCVM]:
    """Documentos IPE das empresas da cobertura ({ticker: cnpj}), mais recentes primeiro."""
    if ipe.empty:
        return []
    por_cnpj = {_so_digitos(c): t for t, c in cnpjs.items() if c}
    df = ipe.assign(_cnpj=ipe["CNPJ_Companhia"].map(_so_digitos))
    df = df[df["_cnpj"].isin(por_cnpj)]
    if categoria:
        df = df[df["Categoria"].str.contains(categoria, case=False, na=False)]
    df = df.sort_values(["Data_Entrega", "Versao"], ascending=False)
    return [
        DocCVM(
            ticker=por_cnpj[r["_cnpj"]],
            cnpj=r["CNPJ_Companhia"],
            categoria=str(r.get("Categoria", "")),
            assunto=str(r.get("Assunto", "") or ""),
            data_referencia=str(r.get("Data_Referencia", "")),
            data_entrega=str(r.get("Data_Entrega", "")),
            versao=str(r.get("Versao", "")),
            link=str(r.get("Link_Download", "")),
        )
        for _, r in df.iterrows()
    ]


def texto_pdf(link: str) -> str:
    from pypdf import PdfReader

    r = requests.get(link, headers=HEADERS, timeout=90)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        return r.content[:4000].decode("utf-8", errors="replace")
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages)


def diagnostico(cnpjs: dict[str, str], hoje: date, detalhar: tuple[str, ...] = ("MGLU3",)) -> None:
    """Imprime o que a CVM tem: calendários recentes da cobertura e o texto dos detalhados."""
    ipe = ler_ipe([hoje.year])
    print(f"\n== CVM IPE {hoje.year}: {len(ipe)} documentos; colunas: {list(ipe.columns)}")
    docs = documentos(ipe, cnpjs)
    cats = sorted({d.categoria for d in docs})
    print(f"  categorias da cobertura: {cats}")
    cal = [d for d in docs if "calend" in d.categoria.lower()]
    vistos = set()
    for d in cal:
        if d.ticker in vistos:
            continue
        vistos.add(d.ticker)
        print(f"  {d.ticker}: calendário entregue {d.data_entrega} v{d.versao} ref {d.data_referencia} | {d.assunto[:60]}")
    print(f"  sem calendário em {hoje.year}: {sorted(set(cnpjs) - vistos)}")
    for t in detalhar:
        recentes = [d for d in docs if d.ticker == t][:8]
        print(f"\n== {t}: últimos documentos IPE")
        for d in recentes:
            print(f"  {d.data_entrega} v{d.versao} | {d.categoria} | {d.assunto[:80]} | {d.link}")
        doc = next((d for d in cal if d.ticker == t), None)
        if doc:
            try:
                txt = texto_pdf(doc.link)
                print(f"\n== {t}: texto do calendário {doc.data_entrega} v{doc.versao} ({len(txt)} caracteres)\n{txt[:6000]}")
            except Exception as e:
                print(f"  falha ao ler o PDF: {e}")
