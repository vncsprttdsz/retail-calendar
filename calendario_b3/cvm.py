"""Calendário de Eventos Corporativos entregue pela companhia à CVM (dados abertos, IPE).

A planilha consolidada da B3 é atualizada de tempos em tempos; quando a companhia
reapresenta o calendário na CVM (ex.: remarca a divulgação do 3T), a data nova
aparece aqui antes.
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests

log = logging.getLogger(__name__)

IPE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (retail-calendar)"}
# Sobe quando a leitura do PDF muda: entradas antigas do cache são relidas.
VERSAO_PARSER = 4


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

    # O RAD às vezes devolve a página HTML do ENET em vez do PDF: tenta de novo e, se
    # persistir, falha (não pode virar "calendário sem datas" no cache).
    m = re.search(r"numProtocolo=(\d+)", link)
    alternativo = (
        "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?Tela=ext&descTipo=IPE&CodigoInstituicao=1"
        f"&numProtocolo={m[1]}" if m else link
    )
    for tentativa in range(3):
        # 1ª tentativa no link original; depois, só com o protocolo (mais estável no RAD).
        r = requests.get(link if tentativa == 0 else alternativo, headers=HEADERS, timeout=90)
        r.raise_for_status()
        if r.content.startswith(b"%PDF"):
            return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages)
        # Página intermediária do RAD: segue o link do arquivo, se houver.
        m = re.search(r"""(?:src|href)=["']([^"']*(?:Download|ExibirArquivo|\.pdf)[^"']*)["']""", r.text, re.I)
        if m and m[1] != link:
            seguinte = requests.compat.urljoin(r.url, m[1].replace("&amp;", "&"))
            r2 = requests.get(seguinte, headers=HEADERS, timeout=90)
            if r2.ok and r2.content.startswith(b"%PDF"):
                return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r2.content)).pages)
        time.sleep(3 * (tentativa + 1))
    raise ValueError("a CVM devolveu HTML em vez do PDF")


_DATA = r"(\d{2}/\d{2}/\d{4})"


def _data(s: str) -> date:
    d, m, a = map(int, s.split("/"))
    return date(a, m, d)


def _preparar(texto: str) -> str:
    t = re.sub(r"[ \t\xa0]+", " ", texto)
    # Versões mais novas do PDF quebram a linha antes da data ("...3º trimestre\n09/11/2026")
    # e depois de "Data de referência:": junta de volta para ficar no formato de uma linha.
    t = re.sub(r"[ ]*\n[ ]*(?=\d{2}/\d{2}/\d{4}\s*(?:\n|$))", " ", t)
    return re.sub(r":[ ]*\n[ ]*", ": ", t)


def datas_de_call(texto: str) -> dict[str, date]:
    """Datas da apresentação pública (call) de resultados no calendário da CVM.

        Apresentação Pública sobre Divulgação de Resultados
        Referentes ao exercício social 13/03/2026      -> 4Q25
        Referentes ao 3º trimestre 10/11/2026          -> 3Q26
    ou, em outros modelos, "Lista de Reuniões Públicas com Analistas / Call de Resultados 3T26 13/11/2026".
    """
    t = _preparar(texto)
    ref = re.search(r"Data de refer[êe]ncia:\s*(\d{4})", t, re.I)
    linhas = [l.strip() for l in t.splitlines()]
    saida: dict[str, date] = {}
    for i, l in enumerate(linhas):
        if re.match(r"Apresenta[çc][ãa]o P[úu]blica", l, re.I) and ref:
            ano = int(ref[1])
            for item in linhas[i + 1 :]:
                m = re.match(r"Referentes? ao (?:([1-3])\s*[º°o]?\s*trimestre|exerc[íi]cio social)\D{0,40}?" + _DATA, item, re.I)
                if not m:
                    break
                q = f"{m[1]}Q{ano % 100:02d}" if m[1] else f"4Q{(ano - 1) % 100:02d}"
                saida.setdefault(q, _data(m[2]))
        elif re.match(r"Lista de Reuni[õo]es P[úu]blicas", l, re.I):
            for item in linhas[i + 1 :]:
                m = re.search(r"\b([1-4])\s*[TQ]\s*(?:20)?(\d{2})\b.*?" + _DATA, item, re.I)
                if not m:
                    break
                saida.setdefault(f"{m[1]}Q{m[2]}", _data(m[3]))
    return saida


def datas_de_resultado(texto: str) -> dict[str, date]:
    """Datas de ITR/DFP no texto do 'Calendário Anual de Eventos Corporativos' da CVM.

        Data de referência: 2026
        ...Padronizadas – DFP relativas ao exercício social findo em 31/12/2025 12/03/2026
        Informações Trimestrais – ITR
        Referentes ao 3º trimestre 05/11/2026
    -> {"4Q25": 12/03/2026, "3Q26": 05/11/2026}
    """
    t = _preparar(texto)
    saida: dict[str, date] = {}
    m = re.search(r"Padronizadas\s*[–-]\s*DFP.{0,200}?findo em\s*\d{2}/\d{2}/(\d{4})\s*" + _DATA, t, re.S | re.I)
    if m:
        saida[f"4Q{int(m[1]) % 100:02d}"] = _data(m[2])
    ref = re.search(r"Data de refer[êe]ncia:\s*(\d{4})", t, re.I)
    # Só as linhas logo abaixo de "Informações Trimestrais – ITR". Outras seções repetem
    # "Referentes ao 3º trimestre" com outras datas: ITR em inglês, apresentação pública (call).
    linhas = [l.strip() for l in t.splitlines()]
    for i, l in enumerate(linhas):
        if re.match(r"Informa[çc][õo]es Trimestrais\s*[–-]\s*ITR\b", l, re.I):
            for item in linhas[i + 1 :]:
                m = re.match(r"Referentes? ao ([1-3])\s*[º°o]?\s*trimestre\D{0,40}?" + _DATA, item, re.I)
                if not m:
                    break
                dia = _data(m[2])
                ano = int(ref[1]) if ref else dia.year
                saida.setdefault(f"{m[1]}Q{ano % 100:02d}", dia)
            break
    return saida


def dentro_do_prazo(rotulo: str, dia: date, folga: int = 0) -> bool:
    """Prazo legal da CVM: ITR até 45 dias após o trimestre; DFP até 3 meses após o exercício.

    Uma data fora do prazo indica leitura errada do PDF (ou outro evento, como o call).
    """
    q, ano = int(rotulo[0]), 2000 + int(rotulo[2:])
    if q == 4:
        return date(ano, 12, 31) < dia <= date(ano + 1, 4, 5) + timedelta(days=folga)  # 31/03 + fim de semana
    fim_tri = {1: date(ano, 3, 31), 2: date(ano, 6, 30), 3: date(ano, 9, 30)}[q]
    return fim_tri < dia <= fim_tri + timedelta(days=47 + folga)


def calendarios_recentes(ipe: pd.DataFrame, cnpjs: dict[str, str]) -> list[DocCVM]:
    """Último calendário entregue por empresa e ano de referência (reapresentações substituem)."""
    vistos, saida = set(), []
    for d in documentos(ipe, cnpjs, categoria="Calend"):
        k = (d.ticker, d.data_referencia[:4])
        if k not in vistos:
            vistos.add(k)
            saida.append(d)
    return saida


def coletar(cnpjs: dict[str, str], nomes: dict[str, str], hoje: date, cache: dict, extras: list[DocCVM] = ()) -> list:
    """Eventos futuros segundo os calendários entregues à CVM.

    Fontes: dados abertos (IPE, atrasam ~1 semana) e `extras` (ex.: Plantão de Notícias
    da B3, no mesmo dia). Os documentos são aplicados do mais antigo para o mais novo:
    a última entrega define a data de cada trimestre.
    `cache` ({"vN link": {rotulo: 'AAAA-MM-DD'}}) evita baixar de novo PDFs já lidos.
    """
    from .modelo import Evento

    # O calendário de um ano costuma ser entregue em dezembro do ano anterior: lê os dois arquivos.
    ipe = ler_ipe([hoje.year - 1, hoje.year])
    for k in [k for k in cache if not k.startswith(f"v{VERSAO_PARSER} ")]:
        del cache[k]  # leitura de versão anterior do parser
    docs = sorted([*calendarios_recentes(ipe, cnpjs), *extras], key=lambda d: d.data_entrega)
    vigentes: dict[tuple[str, str], tuple[date, str]] = {}  # (ticker, trimestre) -> (data, entregue em)
    calls: dict[tuple[str, str], tuple[date, str]] = {}
    for doc in docs:
        chave = f"v{VERSAO_PARSER} {doc.link}"
        if chave not in cache:
            try:
                texto = texto_pdf(doc.link)
                lidas = datas_de_resultado(texto)
                if not lidas:
                    raise ValueError("nenhuma data de ITR/DFP reconhecida")
                cache[chave] = {k: v.isoformat() for k, v in lidas.items()}
                cache[chave].update({f"call {k}": v.isoformat() for k, v in datas_de_call(texto).items()})
            except Exception as e:
                log.warning("%s: não consegui ler o calendário da CVM (%s): %s", doc.ticker, doc.link, e)
                continue
        todas = {k: date.fromisoformat(v) for k, v in cache[chave].items()}
        datas = {k: d for k, d in todas.items() if not k.startswith("call ")}
        for k, d in todas.items():
            q = k[len("call "):]
            if k.startswith("call ") and dentro_do_prazo(q, d, folga=10):
                calls[(doc.ticker, q)] = (d, doc.data_entrega)
        fora = {q: d for q, d in datas.items() if not dentro_do_prazo(q, d)}
        if fora:
            log.warning("%s: datas fora do prazo legal no calendário da CVM, ignoradas: %s (%s)", doc.ticker,
                        ", ".join(f"{q} {d:%d/%m/%Y}" for q, d in sorted(fora.items())), doc.link)
        for q, dia in datas.items():
            if q not in fora:
                vigentes[(doc.ticker, q)] = (dia, doc.data_entrega)
        log.info("%s: calendário %s %s v%s -> %s", doc.ticker, doc.categoria, doc.data_entrega, doc.versao,
                 ", ".join(f"{q} {d:%d/%m}" for q, d in sorted(datas.items())))
    eventos = [
        Evento(t, nomes.get(t, t), f"Resultado {q}", dia, extras={"fonte": "CVM", "entregue": entregue})
        for (t, q), (dia, entregue) in sorted(vigentes.items())
        if dia >= hoje
    ]
    eventos += [
        Evento(t, nomes.get(t, t), f"Call {q}", dia, extras={"fonte": "CVM", "entregue": entregue})
        for (t, q), (dia, entregue) in sorted(calls.items())
        if dia >= hoje
    ]
    return eventos


def diagnostico(cnpjs: dict[str, str], hoje: date, detalhar: tuple[str, ...] = (), nomes: dict | None = None) -> None:
    """Imprime o que a CVM tem: atualização dos dados, nome por CNPJ e todos os calendários."""
    anos = [hoje.year - 1, hoje.year]
    ipe = ler_ipe(anos)
    print(f"\n== CVM IPE {anos}: {len(ipe)} documentos; entrega mais recente nos dados: {ipe['Data_Entrega'].max()}")
    docs = documentos(ipe, cnpjs)
    nomes = {}
    if not ipe.empty:
        df = ipe.assign(_c=ipe["CNPJ_Companhia"].map(_so_digitos))
        nomes = df.drop_duplicates("_c", keep="last").set_index("_c")["Nome_Companhia"].to_dict()
    for t, c in cnpjs.items():
        meus = [d for d in docs if d.ticker == t]
        cal = [d for d in meus if "calend" in d.categoria.lower()]
        print(f"\n  {t} {c} = {nomes.get(_so_digitos(c), 'CNPJ SEM DOCUMENTOS NA CVM')} | {len(meus)} docs, último {meus[0].data_entrega if meus else '-'}")
        for d in cal:
            print(f"      calendário ref {d.data_referencia[:4]} entregue {d.data_entrega} v{d.versao}")
        if not cal:
            print("      nenhum calendário em", anos)
        if not meus and nomes and not ipe.empty:  # CNPJ errado/vazio: candidatos pelo nome
            padrao = "|".join(re.escape(n) for n in nomes.get(t, []) if len(n) >= 4)
            if padrao:
                achados = ipe[ipe["Nome_Companhia"].str.contains(padrao, case=False, na=False)]
                for (cnpj, nome), g in achados.groupby(["CNPJ_Companhia", "Nome_Companhia"]):
                    print(f"      candidato: {cnpj} = {nome} ({len(g)} docs)")
    for t in detalhar:
        recentes = [d for d in docs if d.ticker == t][:8]
        print(f"\n== {t}: últimos documentos IPE")
        for d in recentes:
            print(f"  {d.data_entrega} v{d.versao} | {d.categoria} | {d.assunto[:80]} | {d.link}")
        doc = next((d for d in docs if d.ticker == t and "calend" in d.categoria.lower()), None)
        if doc:
            try:
                txt = texto_pdf(doc.link)
                print(f"\n== {t}: texto do calendário {doc.data_entrega} v{doc.versao} ({len(txt)} caracteres)\n{txt[:6000]}")
            except Exception as e:
                print(f"  falha ao ler o PDF ({doc.link}): {e}")
