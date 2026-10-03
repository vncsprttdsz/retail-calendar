import io
from datetime import date, datetime, time, timezone
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from calendario_b3 import __main__ as cli
from calendario_b3 import fonte, historico, ics, parser
from calendario_b3.filtros import carregar_cobertura, filtrar
from calendario_b3.modelo import Evento, LinhaFonte

PADROES = ["resultado", r"\bITR\b", r"\bDFP\b", "demonstra", "teleconfer"]
COBERTURA = carregar_cobertura(
    [
        {"ticker": "LREN3", "nomes": ["Lojas Renner"]},
        {"ticker": "MGLU3", "nomes": ["Magazine Luiza"]},
        {"ticker": "ASAI3", "nomes": ["Assai", "Sendas"]},
    ]
)


def xlsx(linhas: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Cronograma"
    for linha in linhas:
        ws.append(linha)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def planilha_longa() -> bytes:
    return xlsx(
        [
            ["Cronograma de Eventos Corporativos"],
            ["Atualizado em 03/10/2026"],
            [],
            ["Empresa", "Código", "Evento", "Data do Evento", "Horário"],
            ["LOJAS RENNER S.A.", "LREN3", "Divulgação de Resultados 3T26", datetime(2026, 10, 29), "18:00"],
            [None, None, "Teleconferência de Resultados", datetime(2026, 10, 30, 10, 0), None],
            ["MAGAZINE LUIZA S.A.", "MGLU3", "Divulgação de Resultados 3T26", "06/11/2026", None],
            ["MAGAZINE LUIZA S.A.", "MGLU3", "Assembleia Geral Extraordinária", "20/11/2026", None],
            ["PETROBRAS", "PETR4", "Divulgação de Resultados 3T26", "05/11/2026", None],
        ]
    )


def planilha_larga() -> bytes:
    return xlsx(
        [
            ["Emissor", "Razão Social", "ITR 3T26", "DFP 2026", "AGO"],
            ["LREN", "LOJAS RENNER S.A.", datetime(2026, 10, 29), datetime(2027, 2, 25), datetime(2027, 4, 20)],
            ["ASAI", "SENDAS DISTRIBUIDORA S.A.", datetime(2026, 11, 3), "", ""],
            ["VALE", "VALE S.A.", datetime(2026, 10, 23), datetime(2027, 2, 19), ""],
        ]
    )


def eventos_de(conteudo: bytes, nome="x.xlsx"):
    linhas = []
    for _, df in parser.tabelas_de_arquivo(conteudo, nome):
        linhas += parser.extrair_linhas(df)
    return filtrar(linhas, COBERTURA, PADROES, incluir_todos=False)


def test_formato_longo_com_titulo_e_celulas_mescladas():
    eventos = eventos_de(planilha_longa())
    resumo = [(e.ticker, e.evento, e.data, e.hora) for e in eventos]
    assert resumo == [
        ("LREN3", "Divulgação de Resultados 3T26", date(2026, 10, 29), time(18, 0)),
        ("LREN3", "Teleconferência de Resultados", date(2026, 10, 30), time(10, 0)),
        ("MGLU3", "Divulgação de Resultados 3T26", date(2026, 11, 6), None),
    ]


def test_formato_largo_usa_cabecalho_como_evento():
    eventos = eventos_de(planilha_larga())
    resumo = [(e.ticker, e.evento, e.data) for e in eventos]
    assert resumo == [
        ("LREN3", "Resultado 3Q26", date(2026, 10, 29)),
        ("ASAI3", "Resultado 3Q26", date(2026, 11, 3)),
        ("LREN3", "Resultado 4Q26", date(2027, 2, 25)),
    ]


def test_incluir_todos_traz_assembleia():
    linhas = []
    for _, df in parser.tabelas_de_arquivo(planilha_longa(), "x.xlsx"):
        linhas += parser.extrair_linhas(df)
    eventos = filtrar(linhas, COBERTURA, PADROES, incluir_todos=True)
    assert any("Assembleia" in e.evento for e in eventos)
    assert not any(e.ticker == "PETR4" for e in eventos)


def test_csv_ponto_e_virgula_sem_coluna_de_codigo():
    csv = "Companhia;Evento;Data\nLojas Renner S.A.;Resultado 3T26;29/10/2026\nOutra;Resultado;01/11/2026\n"
    eventos = eventos_de(csv.encode("cp1252"), "x.csv")
    assert [(e.ticker, e.data) for e in eventos] == [("LREN3", date(2026, 10, 29))]


def test_colunas_forcadas():
    df = pd.DataFrame([["Cia", "O que", "Quando"], ["Magazine Luiza", "Resultados", "06/11/2026"]])
    linhas = parser.extrair_linhas(df, {"empresa": "Cia", "evento": "O que", "data": "Quando"})
    assert [(l.empresa, l.data) for l in linhas] == [("Magazine Luiza", date(2026, 11, 6))]


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("29/10/2026", (date(2026, 10, 29), None)),
        ("29/10/26 às 18h30", (date(2026, 10, 29), time(18, 30))),
        ("2026-10-29", (date(2026, 10, 29), None)),
        (46324, (date(2026, 10, 29), None)),
        (pd.Timestamp("2026-10-29 09:00"), (date(2026, 10, 29), time(9, 0))),
        ("a definir", None),
        ("31/02/2026", None),
    ],
)
def test_parse_data(valor, esperado):
    assert parser.parse_data(valor) == esperado


def test_links_de_arquivo_prioriza_cronograma():
    html = """
      <a href="/pt_br/outra-pagina/">Outra</a>
      <a href="/data/files/AA/manual.pdf">Manual</a>
      <a href="/data/files/11/22/Outro.xlsx">Tarifas</a>
      <a href="/lumis/portal/file/fileDownload.jsp?fileId=8AA8">Cronograma de Eventos Corporativos (xlsx)</a>
    """
    links = fonte.links_de_arquivo(html, "https://www.b3.com.br/pt_br/x/")
    assert links == [
        "https://www.b3.com.br/lumis/portal/file/fileDownload.jsp?fileId=8AA8",
        "https://www.b3.com.br/data/files/11/22/Outro.xlsx",
    ]


def test_historico_mantem_passado_e_descarta_futuro_remarcado():
    agora = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    passado = Evento("LREN3", "Renner", "Resultados 2T26", date(2026, 7, 30), visto_em="2026-07-01T00:00:00Z")
    remarcado = Evento("MGLU3", "Magalu", "Resultados 3T26", date(2026, 11, 5), visto_em="2026-09-01T00:00:00Z")
    fora = Evento("PETR4", "Petrobras", "Resultados 2T26", date(2026, 8, 1))
    mantido = Evento("LREN3", "Renner", "Resultados 3T26", date(2026, 10, 29), visto_em="2026-09-15T00:00:00Z")
    novos = [
        Evento("LREN3", "Renner", "Resultados 3T26", date(2026, 10, 29)),
        Evento("MGLU3", "Magalu", "Resultados 3T26", date(2026, 11, 6)),
    ]
    r = historico.mesclar([passado, remarcado, fora, mantido], novos, date(2026, 10, 3), {"LREN3", "MGLU3"}, agora)
    assert [(e.ticker, e.data, e.visto_em) for e in r] == [
        ("LREN3", date(2026, 7, 30), "2026-07-01T00:00:00Z"),
        ("LREN3", date(2026, 10, 29), "2026-09-15T00:00:00Z"),
        ("MGLU3", date(2026, 11, 6), "2026-10-03T12:00:00Z"),
    ]


def test_ics_valido_e_estavel():
    eventos = [
        Evento("LREN3", "Lojas Renner", "Divulgação de Resultados", date(2026, 10, 29), periodo="3T26", visto_em="2026-10-01T00:00:00Z"),
        Evento("MGLU3", "Magazine Luiza", "Teleconferência, resultados; 3T26", date(2026, 11, 7), time(10, 0), visto_em="2026-10-01T00:00:00Z"),
    ]
    a = ics.gerar(eventos, "Cal", "Desc", "America/Sao_Paulo", 60)
    b = ics.gerar(eventos, "Cal", "Desc", "America/Sao_Paulo", 60)
    assert a == b  # sem timestamps variáveis -> sem commits desnecessários
    assert "SUMMARY:LREN Divulgação de Resultados 3T26" in a
    assert "DESCRIPTION" not in a
    assert "DTSTART;VALUE=DATE:20261029" in a and "DTEND;VALUE=DATE:20261030" in a
    assert "DTSTART:20261107T130000Z" in a  # 10h BRT = 13h UTC
    assert "SUMMARY:MGLU Teleconferência\\, resultados\\; 3T26" in a
    assert all(len(l.encode()) <= 75 for l in a.split("\r\n"))
    assert a.count("BEGIN:VEVENT") == 2


COVERAGE_YAML = """
companies:
  - {ticker: LREN3, name: Renner, sector: Apparel, exchange: B3, currency: BRL}
  - {ticker: CEAB3, name: C&A, sector: Apparel, exchange: B3, currency: BRL}
  - {ticker: MGLU3, name: Magazine Luiza, sector: E-commerce, exchange: B3, currency: BRL}
  - {ticker: MELI, name: MercadoLibre, sector: E-commerce, exchange: NASDAQ, currency: USD}
"""


def test_cobertura_do_retail_coverage(tmp_path: Path, monkeypatch):
    (tmp_path / "coverage.yaml").write_text(COVERAGE_YAML, encoding="utf-8")
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    cfg = {
        "arquivo": "coverage.yaml",
        "nomes_extras": {"CEAB3": ["C&A Modas"], "LREN3": ["Lojas Renner"]},
        "empresas": [{"ticker": "VIVA3", "nomes": ["Vivara"]}],
    }
    cob = {e.ticker: e.nomes for e in carregar_cobertura(cfg, tmp_path)}
    assert cob == {
        "LREN3": ["Renner", "Lojas Renner"],
        "CEAB3": ["C&A Modas"],  # "C&A" é curto demais para busca por nome
        "MGLU3": ["Magazine Luiza"],
        "VIVA3": ["Vivara"],
    }  # MELI (NASDAQ) fica de fora


def test_cobertura_arquivo_ausente_falha(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    with pytest.raises(FileNotFoundError):
        carregar_cobertura({"arquivo": "nao-existe.yaml"}, tmp_path)


def test_cli_ponta_a_ponta(tmp_path: Path, monkeypatch):
    arq = tmp_path / "cronograma.xlsx"
    arq.write_bytes(planilha_longa())
    cov = tmp_path / "coverage.yaml"
    cov.write_text(COVERAGE_YAML, encoding="utf-8")
    monkeypatch.setenv("COVERAGE_FILE", str(cov))
    saida, hist = tmp_path / "cal.ics", tmp_path / "eventos.json"
    argv = ["--arquivo", str(arq), "--saida", str(saida), "--historico", str(hist)]
    assert cli.main(argv) == 0
    texto = saida.read_text(encoding="utf-8")
    assert texto.count("BEGIN:VEVENT") == 3
    assert "PETR4" not in texto
    primeira = saida.read_bytes()
    assert cli.main(argv) == 0
    assert saida.read_bytes() == primeira


def test_cli_nao_sobrescreve_quando_nada_e_lido(tmp_path: Path):
    arq = tmp_path / "vazio.csv"
    arq.write_text("nada;aqui\n1;2\n")
    saida = tmp_path / "cal.ics"
    saida.write_text("ANTIGO")
    argv = ["--arquivo", str(arq), "--saida", str(saida), "--historico", str(tmp_path / "h.json")]
    assert cli.main(argv) == 2
    assert saida.read_text() == "ANTIGO"


def test_html_sem_tabela_nao_quebra():
    assert parser.tabelas_de_html("<html><body><p>sem tabela</p></body></html>") == []


def test_celulas_vazias_de_data_nat():
    df = pd.DataFrame(
        [["Empresa", "Evento", "Data"], ["Lojas Renner", "Resultado 3T26", pd.NaT], ["Lojas Renner", "Resultado 4T26", pd.Timestamp("2027-02-25")]]
    )
    assert parser.parse_data(pd.NaT) is None
    assert [l.data for l in parser.extrair_linhas(df)] == [date(2027, 2, 25)]


def planilha_b3_real() -> bytes:
    """Mesmo layout do arquivo publicado pela B3 (aba ANO, cabeçalho em 3 linhas)."""
    return xlsx(
        [
            ["NOME DE PREGÃO", "SEGMENTO", None, None, "Formulário de Referência", None,
             "Informações do 1º Trimestre - ITR", None, "Informações do 2º Trimestre - ITR", None,
             "Informações do 3º Trimestre - ITR", None, None, "Atualizado", datetime(2026, 9, 24)],
            [None, None, "Padronizadas - DFP"],
            [None, None, "Previsão", "Entrega", "Previsão", "Entrega", "Previsão", "Entrega",
             "Previsão", "Entrega", "Previsão", "Entrega"],
            ["PETZCOBASI", "NM|A", "26/03/2026", "26/03/2026", "28/05/2026", datetime(2026, 5, 28),
             "07/05/2026", "07/05/2026", "13/08/2026", datetime(2026, 8, 13), "12/11/2026", None],
            ["LOJAS RENNER", "NM|A", "30/03/2026", "31/03/2026", "29/05/2026", datetime(2026, 5, 29),
             "14/05/2026", "14/05/2026", datetime(2026, 8, 14), datetime(2026, 8, 14), datetime(2026, 11, 11), None],
            ["GRUPO SALTA", "N2|A", "24/02/2026", "24/02/2026", "29/05/2026", None,
             "07/05/2026", "07/05/2026", "06/08/2026", None, "12/11/2026", None],
        ]
    )


def test_layout_real_da_b3():
    linhas = []
    for _, df in parser.tabelas_de_arquivo(planilha_b3_real(), "b3.xlsx"):
        linhas += parser.extrair_linhas(df)
    cobertura = carregar_cobertura([{"ticker": "AUAU3", "nomes": ["PETZCOBASI"]}, {"ticker": "LREN3", "nomes": ["LOJAS RENNER"]}])
    eventos = filtrar(linhas, cobertura, PADROES, incluir_todos=False)
    assert [(e.ticker, e.evento, e.data) for e in eventos] == [
        ("AUAU3", "Resultado 4Q25", date(2026, 3, 26)),
        ("LREN3", "Resultado 4Q25", date(2026, 3, 31)),  # entrega prevalece sobre previsão
        ("AUAU3", "Resultado 1Q26", date(2026, 5, 7)),
        ("LREN3", "Resultado 1Q26", date(2026, 5, 14)),
        ("AUAU3", "Resultado 2Q26", date(2026, 8, 13)),
        ("LREN3", "Resultado 2Q26", date(2026, 8, 14)),
        ("LREN3", "Resultado 3Q26", date(2026, 11, 11)),  # sem entrega -> previsão
        ("AUAU3", "Resultado 3Q26", date(2026, 11, 12)),
    ]
    # Formulário de Referência não é resultado e fica de fora
    assert not any("Formul" in e.evento for e in eventos)


def test_titulo_no_formato_pedido():
    e = Evento("TFCO4", "TRACK FIELD", parser.titulo_evento("Informações do 3º Trimestre - ITR", date(2026, 11, 12)), date(2026, 11, 12))
    assert ics.titulo(e) == "TFCO Resultado 3Q26"
    dfp = Evento("LREN3", "LOJAS RENNER", parser.titulo_evento("Padronizadas - DFP", date(2026, 3, 5)), date(2026, 3, 5))
    assert ics.titulo(dfp) == "LREN Resultado 4Q25"
