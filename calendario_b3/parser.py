"""Converte tabelas (xlsx/xls/csv/html) da B3 em LinhaFonte.

O layout do arquivo da B3 não é documentado e pode mudar, então a leitura é
heurística: procura a linha de cabeçalho, identifica as colunas pelo nome e
aceita dois formatos:

* "longo": uma coluna de evento e uma de data (uma linha por evento);
* "largo": uma linha por empresa e várias colunas de data, cada uma sendo um
  evento (ex.: "ITR 1T", "ITR 2T", "DFP").

Se a detecção errar, o mapeamento pode ser forçado em config.yaml (fonte.colunas).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from .modelo import LinhaFonte, normalizar

PAPEIS = ("empresa", "codigo", "evento", "data", "hora", "periodo")

_RE_TICKER = re.compile(r"^[A-Z]{4}(\d{1,2})?(F)?$")
_DATA_IGNORAR = re.compile(r"ATUALIZ|CADASTR|INCLUS|ALTERAC|MODIFIC")
_EXCEL_EPOCH = datetime(1899, 12, 30)


def papel_do_cabecalho(texto: object) -> str | None:
    t = normalizar(texto)
    if not t:
        return None
    if re.search(r"\bHORA|\bHORARIO", t):
        return "hora"
    if t.startswith("DATA") or re.search(r"\bDATA\b", t) or t in ("DIA", "QUANDO"):
        return None if _DATA_IGNORAR.search(t) else "data"
    if re.search(r"\b(CODIGO|COD|TICKER|SIGLA|PAPEL|ATIVO)\b", t):
        return "codigo"
    if re.search(r"\b(EMPRESA|COMPANHIA|CIA|EMISSOR|RAZAO SOCIAL|DENOMINACAO|NOME)\b", t):
        return "empresa"
    if re.search(r"\b(EVENTO|DESCRICAO|ASSUNTO|TIPO|CATEGORIA|DOCUMENTO)\b", t):
        return "evento"
    if re.search(r"\b(PERIODO|REFERENCIA|TRIMESTRE|EXERCICIO)\b", t):
        return "periodo"
    return None


def _vazio(v: object) -> bool:
    try:
        return v is None or bool(pd.isna(v))  # None, NaN, NaT
    except (TypeError, ValueError):
        return False


def parse_data(v: object) -> tuple[date, time | None] | None:
    if _vazio(v):
        return None
    if isinstance(v, pd.Timestamp):
        if pd.isna(v):
            return None
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        t = v.time()
        return v.date(), (t if t != time(0, 0) else None)
    if isinstance(v, date):
        return v, None
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:  # serial do Excel
        dt = _EXCEL_EPOCH + timedelta(days=float(v))
        dt = dt.replace(second=0, microsecond=0)
        return parse_data(dt)
    s = str(v).strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?", s)
    if m:
        try:
            dia = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
        hora = time(int(m[4]), int(m[5])) if m[4] else None
        return dia, (hora if hora != time(0, 0) else None)
    m = re.search(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?:\D{1,4}(\d{1,2})\s*[:hH]\s*(\d{2}))?", s)
    if m:
        d, mth, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        try:
            dia = date(y, mth, d)
        except ValueError:
            return None
        return dia, (time(int(m[4]), int(m[5])) if m[4] else None)
    return None


def parse_hora(v: object) -> time | None:
    if _vazio(v):
        return None
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, time):
        return v
    if isinstance(v, float) and 0 <= v < 1:  # fração de dia do Excel
        minutos = round(v * 24 * 60)
        return time(minutos // 60, minutos % 60)
    m = re.search(r"(\d{1,2})\s*[:hH]\s*(\d{2})?", str(v))
    if m and int(m[1]) < 24:
        return time(int(m[1]), int(m[2] or 0))
    return None


def _texto(v: object) -> str:
    if _vazio(v):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


# --------------------------------------------------------------------------- leitura


def tabelas_de_arquivo(conteudo: bytes, nome: str = "") -> list[tuple[str, pd.DataFrame]]:
    """Retorna [(nome_da_aba, DataFrame sem cabeçalho)]."""
    tipo = detectar_tipo(conteudo, nome)
    if tipo in ("xlsx", "xls"):
        engine = "openpyxl" if tipo == "xlsx" else "xlrd"
        abas = pd.read_excel(io.BytesIO(conteudo), sheet_name=None, header=None, engine=engine)
        return list(abas.items())
    if tipo == "html":
        return tabelas_de_html(conteudo.decode("utf-8", errors="replace"))
    texto = _decodificar(conteudo)
    sep = ";" if texto.count(";") > texto.count(",") else ","
    df = pd.read_csv(io.StringIO(texto), sep=sep, header=None, dtype=str, keep_default_na=False)
    return [("csv", df)]


def tabelas_de_html(html: str) -> list[tuple[str, pd.DataFrame]]:
    try:
        dfs = pd.read_html(io.StringIO(html), flavor="lxml")
    except ValueError:  # nenhuma <table>
        return []
    saida = []
    for i, df in enumerate(dfs):
        cab = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]
        bruto = pd.concat([pd.DataFrame([cab]), pd.DataFrame(df.values)], ignore_index=True)
        saida.append((f"html#{i}", bruto))
    return saida


def detectar_tipo(conteudo: bytes, nome: str = "") -> str:
    if conteudo[:2] == b"PK":
        return "xlsx"
    if conteudo[:4] == b"\xd0\xcf\x11\xe0":
        return "xls"
    inicio = conteudo[:2048].lstrip().lower()
    if inicio.startswith((b"<!doctype html", b"<html")) or b"<table" in inicio:
        return "html"
    ext = nome.lower().rsplit(".", 1)[-1] if "." in nome else ""
    return ext if ext in ("xlsx", "xls", "html") else "csv"


def _decodificar(b: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- estrutura


@dataclass
class Estrutura:
    linha_cabecalho: int
    cabecalho: list[str]
    papeis: dict[str, int]  # papel -> índice de coluna (formato longo)
    colunas_data: list[int]  # todas as colunas de data (formato largo usa todas)

    @property
    def formato(self) -> str:
        return "longo" if "evento" in self.papeis else "largo"


def _parece_ticker(serie: pd.Series) -> float:
    vals = [normalizar(v).replace(" ", "") for v in serie if _texto(v)]
    if not vals:
        return 0.0
    return sum(bool(_RE_TICKER.match(v)) for v in vals) / len(vals)


def _fracao_datas(serie: pd.Series) -> float:
    vals = [v for v in serie if _texto(v)]
    if not vals:
        return 0.0
    return sum(parse_data(v) is not None for v in vals) / len(vals)


def detectar_estrutura(df: pd.DataFrame, forcadas: dict[str, str] | None = None) -> Estrutura | None:
    forcadas = {k: normalizar(v) for k, v in (forcadas or {}).items() if v}
    melhor: tuple[int, int] | None = None  # (pontuação, linha)
    for i in range(min(len(df), 40)):
        linha = [normalizar(v) for v in df.iloc[i].tolist()]
        if forcadas:
            pont = sum(1 for alvo in forcadas.values() if alvo in linha) * 10
        else:
            papeis = {papel_do_cabecalho(c) for c in linha} - {None}
            pont = len(papeis) + (2 if "data" in papeis else 0)
            if "data" not in papeis and {"empresa", "codigo"} & papeis:
                # formato largo: cabeçalhos de evento ("ITR 1T") com datas logo abaixo
                abaixo = df.iloc[i + 1 : i + 21]
                if any(_fracao_datas(abaixo.iloc[:, j]) >= 0.5 for j in range(df.shape[1])):
                    pont += 2
        if pont >= 3 and (melhor is None or pont > melhor[0]):
            melhor = (pont, i)
    if melhor is None:
        return None

    i = melhor[1]
    cab = [_texto(v) for v in df.iloc[i].tolist()]
    corpo = df.iloc[i + 1 :]
    papeis: dict[str, int] = {}
    colunas_data: list[int] = []

    if forcadas:
        norm = [normalizar(c) for c in cab]
        for papel, alvo in forcadas.items():
            if alvo in norm:
                papeis[papel] = norm.index(alvo)
        if "data" in papeis:
            colunas_data = [papeis["data"]]

    for j, c in enumerate(cab):
        papel = papel_do_cabecalho(c)
        if papel is None or j in papeis.values():
            continue
        col = corpo.iloc[:, j]
        if papel == "data":
            if _fracao_datas(col) >= 0.3:
                colunas_data.append(j)
            continue
        # "Emissor"/"Empresa" com conteúdo de ticker é código; "Código" numérico (CVM) não é.
        if papel in ("empresa", "codigo"):
            ticker = _parece_ticker(col) >= 0.6
            papel = "codigo" if ticker else ("empresa" if papel == "empresa" else None)
            if papel is None:
                continue
        papeis.setdefault(papel, j)

    # Colunas sem "data" no nome mas cheias de datas (ex.: "ITR 1T", "DFP") contam no formato largo.
    if "evento" not in papeis:
        for j, c in enumerate(cab):
            if j in colunas_data or j in papeis.values() or not c:
                continue
            if _fracao_datas(corpo.iloc[:, j]) >= 0.5:
                colunas_data.append(j)
        colunas_data.sort()

    if not colunas_data:
        return None
    if "evento" in papeis or forcadas.get("data"):
        papeis.setdefault("data", colunas_data[0])
    if not ({"empresa", "codigo"} & papeis.keys()):
        return None
    return Estrutura(i, cab, papeis, colunas_data)


def extrair_linhas(df: pd.DataFrame, forcadas: dict[str, str] | None = None) -> list[LinhaFonte]:
    est = detectar_estrutura(df, forcadas)
    if est is None:
        return []
    corpo = df.iloc[est.linha_cabecalho + 1 :].reset_index(drop=True)
    # Células mescladas (empresa repetida em várias linhas) chegam vazias: propaga para baixo.
    for papel in ("empresa", "codigo"):
        if papel in est.papeis:
            j = est.papeis[papel]
            corpo.iloc[:, j] = corpo.iloc[:, j].map(lambda v: _texto(v) or None).ffill()

    def campo(row, papel):
        return _texto(row.iloc[est.papeis[papel]]) if papel in est.papeis else ""

    linhas: list[LinhaFonte] = []
    for _, row in corpo.iterrows():
        empresa, codigo = campo(row, "empresa"), campo(row, "codigo")
        if not (empresa or codigo):
            continue
        periodo = campo(row, "periodo")
        hora_col = parse_hora(row.iloc[est.papeis["hora"]]) if "hora" in est.papeis else None
        if est.formato == "longo":
            alvos = [(campo(row, "evento"), est.papeis["data"])]
        else:
            alvos = [(est.cabecalho[j], j) for j in est.colunas_data]
        for nome_evento, j in alvos:
            dt = parse_data(row.iloc[j])
            if dt is None:
                continue
            linhas.append(
                LinhaFonte(
                    empresa=empresa,
                    codigo=codigo,
                    evento=nome_evento,
                    data=dt[0],
                    hora=dt[1] or hora_col,
                    periodo=periodo,
                )
            )
    return linhas


def descrever(df: pd.DataFrame, forcadas: dict[str, str] | None = None, linhas_amostra: int = 30) -> str:
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        amostra = df.head(linhas_amostra).to_string(max_colwidth=28)
    saida = f"  primeiras {linhas_amostra} linhas (cruas):\n{amostra}\n"
    est = detectar_estrutura(df, forcadas)
    if est is None:
        return saida + "  estrutura NÃO reconhecida."
    papeis = {p: est.cabecalho[j] for p, j in est.papeis.items()}
    datas = [est.cabecalho[j] for j in est.colunas_data]
    linhas = extrair_linhas(df, forcadas)
    ex = "\n".join(f"    {l}" for l in linhas[:8])
    return saida + (
        f"  cabeçalho na linha {est.linha_cabecalho + 1}: {est.cabecalho}\n"
        f"  formato: {est.formato} | papéis: {_fmt(papeis)} | colunas de data: {datas}\n"
        f"  {len(linhas)} linhas com data. Exemplos:\n{ex}"
    )


def _fmt(p: dict[str, str]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in p.items())
