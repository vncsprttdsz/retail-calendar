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


_DATA = r"(\d{2}/\d{2}/\d{4})"


def _data(s: str) -> date:
    d, m, a = map(int, s.split("/"))
    return date(a, m, d)


def datas_de_resultado(texto: str) -> dict[str, date]:
    """Datas de ITR/DFP no texto do 'Calendário Anual de Eventos Corporativos' da CVM.

        Data de referência: 2026
        ...Padronizadas – DFP relativas ao exercício social findo em 31/12/2025 12/03/2026
        Informações Trimestrais – ITR
        Referentes ao 3º trimestre 05/11/2026
    -> {"4Q25": 12/03/2026, "3Q26": 05/11/2026}
    """
    t = re.sub(r"[ \t\xa0]+", " ", texto)
    saida: dict[str, date] = {}
    m = re.search(r"Padronizadas\s*[–-]\s*DFP.{0,200}?findo em\s*\d{2}/\d{2}/(\d{4})\s*" + _DATA, t, re.S | re.I)
    if m:
        saida[f"4Q{int(m[1]) % 100:02d}"] = _data(m[2])
    ref = re.search(r"Data de refer[êe]ncia:\s*(\d{4})", t, re.I)
    # Só a seção do ITR: a de "Apresentação Pública" repete "Referentes ao 3º trimestre" com a data do call.
    sec = re.search(r"Informa[çc][õo]es Trimestrais.*?(?=Assembleia|Apresenta[çc][ãa]o P[úu]blica|Altera[çc][õo]es efetuadas|$)", t, re.S | re.I)
    if sec:
        for q, d in re.findall(r"([1-3])\s*[º°o]?\s*trimestre\D{0,40}?" + _DATA, sec[0], re.I):
            dia = _data(d)
            ano = int(ref[1]) if ref else dia.year
            saida[f"{q}Q{ano % 100:02d}"] = dia
    return saida


def calendarios_recentes(ipe: pd.DataFrame, cnpjs: dict[str, str]) -> list[DocCVM]:
    """Último calendário entregue por empresa e ano de referência (reapresentações substituem)."""
    vistos, saida = set(), []
    for d in documentos(ipe, cnpjs, categoria="Calend"):
        k = (d.ticker, d.data_referencia[:4])
        if k not in vistos:
            vistos.add(k)
            saida.append(d)
    return saida


def coletar(cnpjs: dict[str, str], nomes: dict[str, str], hoje: date, cache: dict) -> list:
    """Eventos futuros segundo o último calendário de cada empresa na CVM.

    `cache` ({link: {rotulo: 'AAAA-MM-DD'}}) evita baixar de novo PDFs já lidos.
    """
    from .modelo import Evento

    ipe = ler_ipe(sorted({hoje.year, hoje.year - 1 if hoje.month <= 2 else hoje.year}))
    eventos = []
    for doc in calendarios_recentes(ipe, cnpjs):
        if doc.link not in cache:
            try:
                cache[doc.link] = {k: v.isoformat() for k, v in datas_de_resultado(texto_pdf(doc.link)).items()}
            except Exception as e:
                log.warning("%s: não consegui ler o calendário da CVM (%s): %s", doc.ticker, doc.link, e)
                continue
        datas = {k: date.fromisoformat(v) for k, v in cache[doc.link].items()}
        if not datas:
            log.warning("%s: calendário da CVM sem datas de ITR/DFP reconhecidas: %s", doc.ticker, doc.link)
        for q, dia in datas.items():
            if dia >= hoje:
                eventos.append(Evento(doc.ticker, nomes.get(doc.ticker, doc.ticker), f"Resultado {q}", dia))
        log.info("%s: calendário CVM %s v%s -> %s", doc.ticker, doc.data_entrega, doc.versao,
                 ", ".join(f"{q} {d:%d/%m}" for q, d in sorted(datas.items())))
    return eventos


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
