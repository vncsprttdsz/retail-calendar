import io
import json
import re
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


CONFIG_TESTE = """
calendario: { nome: Teste, fuso: America/Sao_Paulo }
fonte: { pagina: "https://b3" }
eventos: { palavras_chave: ["resultado", "teleconfer"] }
cobertura:
  empresas:
    - { ticker: LREN3, nomes: ["Lojas Renner"] }
    - { ticker: MGLU3, nomes: ["Magazine Luiza"] }
"""


def test_cli_ponta_a_ponta(tmp_path: Path, monkeypatch):
    arq = tmp_path / "cronograma.xlsx"
    arq.write_bytes(planilha_longa())
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_TESTE, encoding="utf-8")
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    saida, hist = tmp_path / "cal.ics", tmp_path / "eventos.json"
    argv = ["--config", str(cfg), "--arquivo", str(arq), "--saida", str(saida), "--historico", str(hist)]
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


# --------------------------------------------------------------------------- exterior (MELI)

from zoneinfo import ZoneInfo

from calendario_b3 import exterior

SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def sem_internet(monkeypatch):
    """Nenhum teste acessa a internet: Yahoo sem data e CVM sem documentos, salvo quando o teste troca."""
    from calendario_b3 import cvm

    from calendario_b3 import noticias

    monkeypatch.setattr(exterior, "consultar_yahoo", lambda simbolo, tz: None)
    monkeypatch.setattr(cvm, "ler_ipe", lambda anos: pd.DataFrame())
    monkeypatch.setattr(noticias, "listar", lambda inicio, fim: [])
    from calendario_b3 import ri as _ri

    monkeypatch.setattr(_ri, "coletar", lambda empresas, hoje: ([], set()))


def test_trimestre_reportado():
    assert exterior.trimestre_reportado(date(2026, 2, 24)) == "4Q25"
    assert exterior.trimestre_reportado(date(2026, 5, 7)) == "1Q26"
    assert exterior.trimestre_reportado(date(2026, 8, 5)) == "2Q26"
    assert exterior.trimestre_reportado(date(2026, 11, 4)) == "3Q26"


def test_yahoo_confirmado_com_horario():
    # 04/11/2026 16:05 ET = 21:05 UTC = 18:05 em Brasília
    ts = int(datetime(2026, 11, 4, 21, 5, tzinfo=timezone.utc).timestamp())
    d = exterior.interpretar_calendar_events({"earningsDate": [ts], "isEarningsDateEstimate": False}, SP)
    assert (d.dia, d.hora, d.estimado) == (date(2026, 11, 4), time(18, 5), False)


def test_yahoo_estimado_intervalo_e_data_sem_horario():
    meia_noite = [int(datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp()), int(datetime(2026, 11, 3, tzinfo=timezone.utc).timestamp())]
    d = exterior.interpretar_calendar_events({"earningsDate": meia_noite}, SP)
    # meia-noite UTC não pode virar 27/10 21h em Brasília
    assert (d.dia, d.hora, d.estimado) == (date(2026, 10, 28), None, True)


def test_coletar_estimado_e_manual_prevalece():
    yahoo = lambda simbolo, tz: exterior.DataYahoo(date(2026, 11, 4), None, estimado=True)
    itens = [{"ticker": "MELI", "nome": "MercadoLibre"}]
    evs, falhas = exterior.coletar(itens, "America/Sao_Paulo", consulta=yahoo)
    assert [(e.ticker, e.evento, e.data) for e in evs] == [("MELI", "Resultado 3Q26 (estimado)", date(2026, 11, 4))]
    assert ics.titulo(evs[0]) == "MELI Resultado 3Q26 (estimado)"

    itens[0]["manual"] = {"3Q26": "2026-11-05 18:00"}
    evs, _ = exterior.coletar(itens, "America/Sao_Paulo", consulta=yahoo)
    assert [(e.evento, e.data, e.hora) for e in evs] == [("Resultado 3Q26", date(2026, 11, 5), time(18, 0))]


def test_yahoo_fora_do_ar_mantem_evento_futuro():
    def quebrado(simbolo, tz):
        raise ConnectionError("yahoo fora")

    evs, falhas = exterior.coletar([{"ticker": "MELI"}], "America/Sao_Paulo", consulta=quebrado)
    assert evs == [] and falhas == {"MELI"}
    antigo = Evento("MELI", "MercadoLibre", "Resultado 3Q26", date(2026, 11, 4), visto_em="2026-10-01T00:00:00Z")
    r = historico.mesclar([antigo], evs, date(2026, 10, 3), {"MELI"}, preservar=falhas)
    assert [e.evento for e in r] == ["Resultado 3Q26"]
    # sem falha, evento futuro que sumiu da fonte sai (remarcado)
    assert historico.mesclar([antigo], [], date(2026, 10, 3), {"MELI"}) == []


def test_data_manual_invalida():
    with pytest.raises(ValueError):
        exterior.coletar([{"ticker": "MELI", "manual": {"3Q26": "04/11/2026"}}], "America/Sao_Paulo")


# --------------------------------------------------------------------------- ajustes e avisos

from calendario_b3 import ajustes, notificar


def test_data_manual_substitui_a_da_b3():
    b3 = [Evento("MGLU3", "MAGAZ LUIZA", "Resultado 3Q26", date(2026, 11, 5)),
          Evento("MGLU3", "MAGAZ LUIZA", "Resultado 2Q26", date(2026, 8, 6))]
    manuais = ajustes.manuais([{"ticker": "MGLU3", "nomes": ["MAGAZ LUIZA"], "manual": {"3Q26": "2026-11-09"}}])
    r = ajustes.substituir(b3, manuais)
    assert sorted((e.evento, e.data) for e in r) == [("Resultado 2Q26", date(2026, 8, 6)), ("Resultado 3Q26", date(2026, 11, 9))]


def test_diferencas_para_o_telegram():
    hoje = date(2026, 10, 8)
    antes = [
        Evento("MGLU3", "M", "Resultado 3Q26", date(2026, 11, 5)),
        Evento("MELI", "ML", "Resultado 4Q26 (estimado)", date(2027, 2, 24)),
        Evento("LREN3", "R", "Resultado 3Q26", date(2026, 11, 5)),
        Evento("VIVA3", "V", "Resultado 3Q26", date(2026, 11, 5)),
        Evento("LREN3", "R", "Resultado 2Q26", date(2026, 8, 6)),  # passado: ignorado
    ]
    depois = [
        Evento("MGLU3", "M", "Resultado 3Q26", date(2026, 11, 9)),
        Evento("MELI", "ML", "Resultado 4Q26", date(2027, 2, 24), time(18, 5)),
        Evento("LREN3", "R", "Resultado 3Q26", date(2026, 11, 5)),
        Evento("ASAI3", "A", "Resultado 3Q26", date(2026, 11, 6)),
    ]
    assert notificar.diferencas(antes, depois, hoje) == [
        "🗑️ VIVA Resultado 3Q26 (05/11) saiu do calendário",
        "🆕 ASAI Resultado 3Q26: 06/11",
        "📅 MGLU Resultado 3Q26: 05/11 → 09/11",
        "📅 MELI Resultado 4Q26: 24/02 → 24/02 18:05",
    ]
    assert notificar.diferencas(antes, antes, hoje) == []


def test_estimado_vira_confirmado_sem_mudar_data():
    hoje = date(2026, 10, 8)
    antes = [Evento("MELI", "ML", "Resultado 3Q26 (estimado)", date(2026, 11, 4))]
    depois = [Evento("MELI", "ML", "Resultado 3Q26", date(2026, 11, 4))]
    assert notificar.diferencas(antes, depois, hoje) == ["✅ MELI Resultado 3Q26: 04/11 (antes: MELI Resultado 3Q26 (estimado))"]


def test_telegram_nao_vaza_token(monkeypatch, caplog):
    import requests as rq

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SEGREDO")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    def falha(url, **kw):
        raise rq.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(notificar.requests, "post", falha)
    assert notificar.enviar(["📅 MGLU Resultado 3Q26: 05/11 → 09/11"]) is False
    assert "SEGREDO" not in caplog.text


def test_telegram_envia_mensagem(monkeypatch):
    enviados = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(notificar.requests, "post", lambda url, json, timeout: enviados.append((url, json)) or Resp())
    assert notificar.enviar(["linha 1", "linha 2"]) is True
    url, corpo = enviados[0]
    assert url.endswith("/bot123:abc/sendMessage") and corpo["chat_id"] == "42"
    assert corpo["text"].endswith("linha 1\nlinha 2")


# --------------------------------------------------------------------------- CVM

from calendario_b3 import cvm

# Texto real (pypdf) do calendário da Magalu entregue à CVM em 29/07/2026, versão 3.
CALENDARIO_MGLU = """CALENDÁRIO ANUAL DE EVENTOS CORPORATIVOS
Denominação Social: MAGAZINE LUIZA S.A.
Data de referência: 2026
Datas programadas para divulgação de informações periódicas e eventuais
Demonstrações Financeiras Anuais Completas e Demonstrações Financeiras
Padronizadas – DFP relativas ao exercício social findo em 31/12/2025 12/03/2026
Formulário de Referência, relativo ao exercício social em curso 29/05/2026
Informe sobre o Código Brasileiro de Governança Corporativa - Companhias Abertas 29/07/2026
Informações Trimestrais – ITR
Referentes ao 1º trimestre 07/05/2026
Referentes ao 2º trimestre 06/08/2026
Referentes ao 3º trimestre 05/11/2026
Assembleia Geral Ordinária
Envio da Proposta da Administração 24/03/2026
Apresentação Pública sobre Divulgação de Resultados
Referentes ao exercício social 13/03/2026
Referentes ao 1º trimestre 08/05/2026
Referentes ao 2º trimestre 07/08/2026
Referentes ao 3º trimestre 06/11/2026
Alterações efetuadas:
Data de divulgação do Código Brasileiro Governança Corporativa Companhias Abertas alterada de 31/07/2026 para 29/07/2026
"""


def test_cvm_le_calendario_real():
    assert cvm.datas_de_resultado(CALENDARIO_MGLU) == {
        "4Q25": date(2026, 3, 12),
        "1Q26": date(2026, 5, 7),
        "2Q26": date(2026, 8, 6),
        "3Q26": date(2026, 11, 5),  # não pega 06/11, que é a apresentação pública (call)
    }


def _ipe(*linhas):
    cols = ["CNPJ_Companhia", "Categoria", "Assunto", "Data_Referencia", "Data_Entrega", "Versao", "Link_Download"]
    return pd.DataFrame([dict(zip(cols, l)) for l in linhas])


def test_cvm_usa_ultima_reapresentacao(monkeypatch):
    ipe = _ipe(
        ("47.960.950/0001-21", "Calendário de Eventos Corporativos", "", "2026-12-31", "2026-07-29", "3", "v3"),
        ("47.960.950/0001-21", "Calendário de Eventos Corporativos", "", "2026-12-31", "2026-10-07", "4", "v4"),
        ("47.960.950/0001-21", "Fato Relevante", "x", "2026-10-07", "2026-10-07", "1", "fr"),
        ("00.000.000/0001-00", "Calendário de Eventos Corporativos", "", "2026-12-31", "2026-10-07", "1", "outra"),
    )
    textos = {"v3": CALENDARIO_MGLU, "v4": CALENDARIO_MGLU.replace("3º trimestre 05/11/2026", "3º trimestre 09/11/2026")}
    baixados = []
    monkeypatch.setattr(cvm, "ler_ipe", lambda anos: ipe)
    monkeypatch.setattr(cvm, "texto_pdf", lambda link: baixados.append(link) or textos[link])
    cache = {}
    evs = cvm.coletar({"MGLU3": "47.960.950/0001-21"}, {"MGLU3": "MAGAZ LUIZA"}, date(2026, 10, 8), cache)
    assert [(e.ticker, e.evento, e.data) for e in evs] == [
        ("MGLU3", "Resultado 3Q26", date(2026, 11, 9)),  # só futuros
        ("MGLU3", "Call 3Q26", date(2026, 11, 6)),  # apresentação pública, do mesmo calendário
    ]
    assert baixados == ["v4"]
    cvm.coletar({"MGLU3": "47.960.950/0001-21"}, {}, date(2026, 10, 8), cache)
    assert baixados == ["v4"]  # segunda rodada usa o cache


def test_cvm_fora_do_ar_nao_derruba(monkeypatch, tmp_path):
    def quebrado(anos):
        raise ConnectionError("cvm fora")

    monkeypatch.setattr(cvm, "ler_ipe", quebrado)
    cfg = {"cobertura": {"empresas": [{"ticker": "MGLU3", "cnpj": "47.960.950/0001-21"}]}}
    assert cli._datas_cvm(cfg, [], date(2026, 10, 8), tmp_path / "c.json") == []


def test_prazo_legal_descarta_leitura_errada():
    assert cvm.dentro_do_prazo("3Q26", date(2026, 11, 12))
    assert not cvm.dentro_do_prazo("3Q26", date(2026, 11, 26))  # TFCO4: leitura errada do PDF
    assert cvm.dentro_do_prazo("4Q25", date(2026, 3, 31))
    assert not cvm.dentro_do_prazo("4Q25", date(2026, 6, 29))


def test_cvm_ignora_data_fora_do_prazo(monkeypatch):
    ipe = _ipe(("59.418.806/0001-47", "Calendário de Eventos Corporativos", "", "2026-12-31", "2026-06-18", "3", "tf"))
    monkeypatch.setattr(cvm, "ler_ipe", lambda anos: ipe)
    monkeypatch.setattr(cvm, "texto_pdf", lambda link: CALENDARIO_MGLU.replace("3º trimestre 05/11/2026", "3º trimestre 26/11/2026"))
    evs = cvm.coletar({"TFCO4": "59.418.806/0001-47"}, {}, date(2026, 10, 8), {})
    assert [e.evento for e in evs if e.evento.startswith("Resultado")] == []


# Texto real do calendário da TFCO4 (CVM, 18/06/2026 v3): tem ITR em inglês e lista de calls.
CALENDARIO_TFCO = """CALENDÁRIO ANUAL DE EVENTOS CORPORATIVOS
Denominação Social: TRACK & FIELD CO S.A.
Data de referência: 2026
Datas programadas para divulgação de informações periódicas e eventuais
Demonstrações Financeiras Anuais Completas e Demonstrações Financeiras Padronizadas –
DFP relativas ao exercício social findo em 31/12/2025 09/03/2026
Demonstrações Financeiras Anuais traduzidas para o inglês, relativas ao exercício social
findo em 31/12/2025 23/03/2026
Formulário de Referência, relativo ao exercício social em curso 01/06/2026
Informações Trimestrais – ITR
Referentes ao 1º trimestre 11/05/2026
Referentes ao 2º trimestre 12/08/2026
Referentes ao 3º trimestre 12/11/2026
 
Informações Trimestrais traduzidas para o inglês
Referentes ao 1º trimestre 25/05/2026
Referentes ao 2º trimestre 26/08/2026
Referentes ao 3º trimestre 26/11/2026
 
Assembleia Geral Ordinária
Lista de Reuniões Públicas com Analistas
Call de Resultados 3T26 13/11/2026
Alterações efetuadas:
Data de divulgação do ITR (2º trimestre) alterada de 13/08/2026 para 12/08/2026
"""


def test_cvm_ignora_itr_em_ingles():
    assert cvm.datas_de_resultado(CALENDARIO_TFCO) == {
        "4Q25": date(2026, 3, 9),
        "1Q26": date(2026, 5, 11),
        "2Q26": date(2026, 8, 12),
        "3Q26": date(2026, 11, 12),
    }


def test_cvm_html_no_lugar_do_pdf_nao_entra_no_cache(monkeypatch):
    ipe = _ipe(("47.960.950/0001-21", "Calendário de Eventos Corporativos", "", "2026-12-31", "2026-07-29", "3", "v3"))
    monkeypatch.setattr(cvm, "ler_ipe", lambda anos: ipe)

    def html(link):
        raise ValueError("a CVM devolveu HTML em vez do PDF")

    monkeypatch.setattr(cvm, "texto_pdf", html)
    cache = {}
    assert cvm.coletar({"MGLU3": "47.960.950/0001-21"}, {}, date(2026, 10, 8), cache) == []
    assert cache == {}  # na próxima rodada tenta de novo
    monkeypatch.setattr(cvm, "texto_pdf", lambda link: "texto sem datas")
    cvm.coletar({"MGLU3": "47.960.950/0001-21"}, {}, date(2026, 10, 8), cache)
    assert cache == {}



# --------------------------------------------------------------------------- Plantão de Notícias

from calendario_b3 import noticias

# Trecho real da página de detalhe (Plantão de Notícias, 08/10/2026).
DETALHE_RIAA = """<pre id="conteudoDetalhe" style="width: auto;white-space: break-spaces;">RIACHUELO (RIAA-NM) -
Calendario de Eventos Corporativos - ate 31/12/26 (R)      https://www.rad.cvm.gov.br/ENETWEB/frmExibirArquivoIPEExterno.aspx?ID=1575266&amp;flnk
(R) = Reapresentacao  (C) = Documento Cancelado  (N) = Norma / Notas</pre>"""


def test_plantao_reapresentacao_prevalece_sobre_dados_abertos(monkeypatch):
    riaa = noticias.Noticia("1", "18", "2026-10-08 08:00:19",
                            "RIACHUELO (RIAA-NM) - Calendario de Eventos Corporativos - ate 31/12/26 (R)", "")
    outra = noticias.Noticia("2", "18", "2026-10-06 17:34:36", "ASSAI (ASAI-NM) - Alienacao de Participacao Acionaria - 06/10/26", "")
    assert riaa.raiz == "RIAA" and outra.raiz == "ASAI"

    class Resp:
        text = DETALHE_RIAA

        def raise_for_status(self):
            pass

    monkeypatch.setattr(noticias, "listar", lambda inicio, fim: [riaa, outra])
    monkeypatch.setattr(noticias.requests, "get", lambda url, **kw: Resp())
    extras = noticias.calendarios(["RIAA3", "ASAI3"], date(2026, 10, 9))
    assert [(d.ticker, d.link) for d in extras] == [
        ("RIAA3", "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?Tela=ext&descTipo=IPE&CodigoInstituicao=1&numProtocolo=1575266")
    ]

    # Dados abertos ainda com a versão de dez/2025 (3Q26 em 04/11); o Plantão traz a nova (11/11).
    ipe = _ipe(("08.402.943/0001-52", "Calendário de Eventos Corporativos", "", "2026-12-31", "2025-12-09", "1", "v1"))
    monkeypatch.setattr(cvm, "ler_ipe", lambda anos: ipe)
    textos = {"v1": CALENDARIO_MGLU.replace("05/11/2026", "04/11/2026"),
              extras[0].link: CALENDARIO_MGLU.replace("05/11/2026", "11/11/2026")}
    monkeypatch.setattr(cvm, "texto_pdf", lambda link: textos[link])
    evs = cvm.coletar({"RIAA3": "08.402.943/0001-52"}, {}, date(2026, 10, 9), {}, extras)
    assert [(e.ticker, e.evento, e.data) for e in evs if e.evento.startswith("Resultado")] == [
        ("RIAA3", "Resultado 3Q26", date(2026, 11, 11))
    ]


# Texto real da reapresentação da RIAA3 (Plantão, 08/10/2026): data na linha de baixo.
CALENDARIO_RIAA_R = """CALENDÁRIO ANUAL DE EVENTOS CORPORATIVOS
Denominação Social: 
GUARARAPES CONFECCOES S.A.
Data de referência: 
2026
Datas programadas para divulgação de informações periódicas e eventuais
 
Demonstrações Financeiras Anuais Completas e Demonstrações Financeiras Padronizadas – DFP relativas ao exercício social findo em
31/12/2025
11/02/2026
 
Formulário de Referência, relativo ao exercício social em curso
29/05/2026
 
Informações Trimestrais – ITR
Referentes ao 1º trimestre
06/05/2026
Referentes ao 2º trimestre
05/08/2026
Referentes ao 3º trimestre
09/11/2026
 
Assembleia Geral Ordinária
Envio da Proposta da Administração
30/03/2026
 
Apresentação Pública sobre Divulgação de Resultados
Referentes ao 3º trimestre
10/11/2026
Alterações efetuadas:
Data de divulgação do ITR (3º trimestre) alterada de 04/11/2026 para 09/11/2026
"""


def test_cvm_le_data_na_linha_de_baixo():
    assert cvm.datas_de_resultado(CALENDARIO_RIAA_R) == {
        "4Q25": date(2026, 2, 11),
        "1Q26": date(2026, 5, 6),
        "2Q26": date(2026, 8, 5),
        "3Q26": date(2026, 11, 9),  # não 10/11, que é o call
    }


def test_cvm_le_data_do_call():
    assert cvm.datas_de_call(CALENDARIO_MGLU) == {
        "4Q25": date(2026, 3, 13), "1Q26": date(2026, 5, 8), "2Q26": date(2026, 8, 7), "3Q26": date(2026, 11, 6),
    }
    assert cvm.datas_de_call(CALENDARIO_TFCO) == {"3Q26": date(2026, 11, 13)}  # "Lista de Reuniões Públicas"
    assert cvm.datas_de_call(CALENDARIO_RIAA_R) == {"3Q26": date(2026, 11, 10)}


# --------------------------------------------------------------------------- sites de RI

from calendario_b3 import ri

# Trechos reais da API mzevents (future), 09/10/2026.
MZ_SBFG = [
    {"event_name": "Divulgação de Resultados 3T26", "event_date": "2026-11-09T12:00:00.000Z", "event_starttime": "",
     "event_endtime": "", "external_link": None, "event_details": "", "advanced": ""},
    {"event_name": "Call de Resultados 3T26", "event_date": "2026-11-10T12:00:00.000Z", "event_starttime": "",
     "event_endtime": "", "external_link": "", "event_details": "", "advanced": ""},
]
MZ_OUTROS = [
    {"event_name": "Período de Silêncio", "event_date": "2026-10-22T12:00:00.000Z", "event_starttime": ""},
    {"event_name": "  Webcast 3T26", "event_date": "2026-11-13T12:00:00.000Z", "event_starttime": "10:00", "event_endtime": "11:00",
     "external_link": "", "event_details": '<p>Inscreva-se: <a href="https://mzgroup.zoom.us/webinar/register/WN_abc">aqui</a></p>'},
    {"event_name": "Divulgação de Resultados 3T2026", "event_date": "2026-11-05T12:00:00.000Z", "event_starttime": ""},
    {"event_name": " Apresentação de Resultados 3T26", "event_date": "2026-11-05T12:00:00.000Z", "event_starttime": "11:00",
     "event_endtime": "12:00", "external_link": "https://ten.com.br/evento/xyz"},
]

HOME_MGLU = """<div>Calendário de Eventos nov 09 Divulgação de Resultados 3T26 Após Fechamento do Mercado nov 10 Call
Apresentação de Resultados 3T26 09:00 - 11:00 (Horário de Brasília) Ver todos os eventos Central de Resultados 2T26</div>
<a href="https://ri.enfoque.com.br/RIWeb/Empresas/cotacao?token=1">cotação</a>"""
HOME_RADL = """Calendário de eventos 03 nov Divulgação Resultados 3T26 Após Fechamento do Mercado 04 nov Call Resultados 3T26
[PT+EN] 10:00 - 11:30 (Horário de Brasília) Ver todos os eventos Últimas Notícias"""


def test_classificar_titulos_reais():
    assert ri.classificar("Divulgação de Resultados 3T2026") == ("resultado", "3Q26")
    assert ri.classificar("Videoconferencia dos Resultados do 3T26") == ("call", "3Q26")
    assert ri.classificar(" Apresentação de Resultados 3T26") == ("call", "3Q26")
    assert ri.classificar("Call Apresentação de Resultados 3T26") == ("call", "3Q26")
    assert ri.classificar("Período de Silêncio") is None
    assert ri.classificar("Assembleia Geral Ordinária 2026") is None


def test_mz_resultado_e_call():
    evs = ri.de_mz("SBFG3", MZ_SBFG) + ri.de_mz("X", MZ_OUTROS)
    assert [(e.ticker, e.tipo, e.rotulo, e.dia, e.inicio, e.fim, e.link) for e in evs] == [
        ("SBFG3", "resultado", "3Q26", date(2026, 11, 9), None, None, ""),
        ("SBFG3", "call", "3Q26", date(2026, 11, 10), None, None, ""),
        ("X", "call", "3Q26", date(2026, 11, 13), time(10), time(11), "https://mzgroup.zoom.us/webinar/register/WN_abc"),
        ("X", "resultado", "3Q26", date(2026, 11, 5), None, None, ""),
        ("X", "call", "3Q26", date(2026, 11, 5), time(11), time(12), "https://ten.com.br/evento/xyz"),
    ]


def test_riweb_magalu_e_rd():
    hoje = date(2026, 10, 9)
    mglu = ri.de_riweb("MGLU3", HOME_MGLU, hoje)
    assert [(e.tipo, e.rotulo, e.dia, e.inicio, e.fim) for e in mglu] == [
        ("resultado", "3Q26", date(2026, 11, 9), None, None),
        ("call", "3Q26", date(2026, 11, 10), time(9), time(11)),
    ]
    radl = ri.de_riweb("RADL3", HOME_RADL, hoje)
    assert [(e.tipo, e.dia, e.inicio, e.fim) for e in radl] == [
        ("resultado", date(2026, 11, 3), None, None), ("call", date(2026, 11, 4), time(10), time(11, 30)),
    ]
    # Em dezembro, "fev 24" é do ano seguinte.
    assert ri.de_riweb("X", "Calendário de Eventos fev 24 Divulgação de Resultados 4T26 Ver todos", date(2026, 12, 10))[0].dia == date(2027, 2, 24)


def test_link_do_popup_vai_para_o_proximo_call(monkeypatch):
    home = 'x <div class="modal"><a href="https://mzgroup.zoom.us/webinar/register/WN_APnoN5">Inscreva-se</a></div>' \
           '<a href="https://www.youtube.com/user/canal">yt</a><img src="https://x/webcast.png">'
    monkeypatch.setattr(ri, "_get", lambda url: type("R", (), {"text": home})())
    monkeypatch.setattr(ri, "mz_id", lambda html: "id")
    monkeypatch.setattr(ri, "mz_eventos", lambda fm, tipo: MZ_SBFG)
    evs = ri.eventos_da_empresa({"ticker": "SBFG3", "ri": ["https://ri"]}, date(2026, 10, 30))
    assert [e.link for e in evs if e.tipo == "call"] == ["https://mzgroup.zoom.us/webinar/register/WN_APnoN5"]
    # Com o call ainda distante (> 21 dias), o link da home não é atribuído.
    evs = ri.eventos_da_empresa({"ticker": "SBFG3", "ri": ["https://ri"]}, date(2026, 10, 9))
    assert [e.link for e in evs if e.tipo == "call"] == [""]


def _cfg_ri():
    return {"cobertura": {"empresas": [{"ticker": "MGLU3", "nomes": ["MAGAZ LUIZA"], "ri": ["https://ri"]}]}}


def test_ri_traz_call_com_horario_e_link(monkeypatch, tmp_path):
    achados = [ri.EventoRI("MGLU3", "resultado", "3Q26", date(2026, 11, 9)),
               ri.EventoRI("MGLU3", "call", "3Q26", date(2026, 11, 10), time(9), time(11), "https://mzgroup.zoom.us/webinar/register/WN_1")]
    monkeypatch.setattr(ri, "coletar", lambda empresas, hoje: (achados, set()))
    b3 = [Evento("MGLU3", "MAGAZ LUIZA", "Resultado 3Q26", date(2026, 11, 9))]
    r = cli._aplicar_ri(_cfg_ri(), b3, [], date(2026, 10, 9), tmp_path / "e.json")
    call = [e for e in r if e.evento == "Call 3Q26"][0]
    assert (call.data, call.hora, call.extras["fim"], call.extras["link"]) == (date(2026, 11, 10), time(9), "11:00", "https://mzgroup.zoom.us/webinar/register/WN_1")
    texto = ics.gerar(r, "Cal", "", "America/Sao_Paulo", 60)
    assert "SUMMARY:MGLU Call 3Q26" in texto
    assert "DTSTART:20261110T120000Z" in texto and "DTEND:20261110T140000Z" in texto  # 9h-11h Brasília
    assert "DESCRIPTION:Webcast: https://mzgroup.zoom.us/webinar/register/WN_1" in texto


def test_ri_diverge_vale_o_mais_recente(monkeypatch, tmp_path):
    estado = tmp_path / "e.json"
    hoje = date(2026, 10, 9)
    ri_dia = [ri.EventoRI("MGLU3", "resultado", "3Q26", date(2026, 11, 12))]
    monkeypatch.setattr(ri, "coletar", lambda empresas, hoje: (ri_dia, set()))
    # B3 (sem data de entrega): o RI prevalece.
    b3 = [Evento("MGLU3", "M", "Resultado 3Q26", date(2026, 11, 9))]
    assert [e.data for e in cli._aplicar_ri(_cfg_ri(), b3, [], hoje, estado)] == [date(2026, 11, 12)]
    # Reapresentação na CVM depois de o RI passar a mostrar 12/11: a CVM prevalece (RI desatualizado).
    estado.write_text('{"MGLU3 resultado 3Q26": {"data": "2026-11-12", "desde": "2026-10-01 10:00:00"}}')
    cvm_ev = [Evento("MGLU3", "M", "Resultado 3Q26", date(2026, 11, 9), extras={"fonte": "CVM", "entregue": "2026-10-07 19:49:14"})]
    assert [e.data for e in cli._aplicar_ri(_cfg_ri(), cvm_ev, [], hoje, estado)] == [date(2026, 11, 9)]
    # RI mudou depois da reapresentação: o RI prevalece.
    estado.write_text('{"MGLU3 resultado 3Q26": {"data": "2026-11-12", "desde": "2026-10-08 12:00:00"}}')
    assert [e.data for e in cli._aplicar_ri(_cfg_ri(), cvm_ev, [], hoje, estado)] == [date(2026, 11, 12)]


def test_ri_fora_do_ar_mantem_call_conhecido(monkeypatch, tmp_path):
    monkeypatch.setattr(ri, "coletar", lambda empresas, hoje: ([], {"MGLU3"}))
    conhecido = Evento("MGLU3", "M", "Call 3Q26", date(2026, 11, 10), time(9), extras={"fonte": "RI", "link": "https://z"})
    sem_hora = Evento("MGLU3", "M", "Call 3Q26", date(2026, 11, 10), extras={"fonte": "CVM"})
    r = cli._aplicar_ri(_cfg_ri(), [sem_hora], [conhecido], date(2026, 10, 9), tmp_path / "e.json")
    assert [(e.hora, e.extras.get("link")) for e in r] == [(time(9), "https://z")]


def test_aviso_de_link_do_webcast():
    hoje = date(2026, 10, 9)
    antes = [Evento("LREN3", "R", "Call 3Q26", date(2026, 11, 6), time(10))]
    depois = [Evento("LREN3", "R", "Call 3Q26", date(2026, 11, 6), time(10), extras={"link": "https://zoom.us/webinar/register/1"})]
    assert notificar.diferencas(antes, depois, hoje) == ["🔗 LREN Call 3Q26 (06/11 10:00): https://zoom.us/webinar/register/1"]
    # Resultado e call do mesmo trimestre são eventos distintos.
    assert notificar.diferencas([], [Evento("LREN3", "R", "Resultado 3Q26", date(2026, 11, 5)), *depois], hoje) == [
        "🆕 LREN Resultado 3Q26: 05/11", "🆕 LREN Call 3Q26: 06/11 10:00"]


def test_riweb_ignora_calendario_do_menu():
    home = ("Menu: Central de Resultados Calendário de Eventos Cobertura de Analistas Ver todos "
            "Fale com RI ... " + HOME_RADL)
    assert [(e.tipo, e.dia) for e in ri.de_riweb("RADL3", home, date(2026, 10, 9))] == [
        ("resultado", date(2026, 11, 3)), ("call", date(2026, 11, 4))]


# Trecho real da home da Renner, 09/10/2026 (a API da MZ traz o call sem horário).
HOME_LREN = ("Divulgação de Resultados 3T26 5 de novembro de 2026 após o fechamento do mercado "
             "Videoconferência: 6 de novembro 10h (Brasil) / 8h (US-ET) Período de silêncio")


def test_horario_do_call_no_texto_da_home(monkeypatch):
    assert ri.horarios_no_texto(HOME_LREN) == {(11, 6): time(10)}
    assert ri.horarios_no_texto("Teleconferência 3T26 em 06/11/2026, às 11h00 (Brasília)") == {(11, 6): time(11)}
    assert ri.horarios_no_texto("Conferência 3T26: 12/11 às 9h30") == {(11, 12): time(9, 30)}
    mz = [{"event_name": "Divulgação de Resultados 3T26", "event_date": "2026-11-05T12:00:00.000Z", "event_starttime": ""},
          {"event_name": "Videoconferência de Resultados 3T26", "event_date": "2026-11-06T12:00:00.000Z", "event_starttime": ""}]
    monkeypatch.setattr(ri, "_get", lambda url: type("R", (), {"text": f"<p>{HOME_LREN}</p>"})())
    monkeypatch.setattr(ri, "mz_id", lambda html: "id")
    monkeypatch.setattr(ri, "mz_eventos", lambda fm, tipo: mz)
    evs = ri.eventos_da_empresa({"ticker": "LREN3", "ri": ["https://ri"]}, date(2026, 10, 9))
    assert [(e.tipo, e.dia, e.inicio) for e in evs] == [
        ("resultado", date(2026, 11, 5), None), ("call", date(2026, 11, 6), time(10))]


# Trecho real do JSON embutido em investor.mercadolibre.com/news-and-events, 09/10/2026.
MELI_EVENTOS = ('<script>window.x={"pastEvents":{"totalElements":0,"rows":[],"pageSize":10}},"upcomingEvents":{"rows":'
                '[{"id":"9a48e910-d239-4ca3-a9e7-490c979f89b6","cells":[{"columnKey":"date","text":"2026-11-04T08:00:00"},'
                '{"columnKey":"event","text":"Q3\'26 Results - Provisional date"},{"columnKey":"details","text":"Provisional date"},'
                '{"columnKey":"addToCalendar","text":"ADD","url":"\\u002Fapi\\u002Fsites\\u002F5dbba919\\u002Fics"}]}],'
                '"totalElements":1,"pageSize":10},"pressReleases":{}}</script>')


def test_meli_agenda_do_site():
    assert [(e.tipo, e.rotulo, e.dia, e.inicio, e.provisorio) for e in ri.de_upcoming("MELI", MELI_EVENTOS)] == [
        ("resultado", "3Q26", date(2026, 11, 4), None, True)]
    confirmado = MELI_EVENTOS.replace("Results - Provisional date", "Earnings Conference Call").replace(
        '"text":"Provisional date"', '"text":"4:30 p.m. ET https://mercadolibre.zoom.us/webinar/register/WN_x"')
    (call,) = ri.de_upcoming("MELI", confirmado)
    # 04/11 já é horário de inverno nos EUA (ET = UTC-5): 16:30 ET = 18:30 em Brasília.
    assert (call.tipo, call.dia, call.inicio, call.provisorio, call.link) == (
        "call", date(2026, 11, 4), time(18, 30), False, "https://mercadolibre.zoom.us/webinar/register/WN_x")
    assert ri.classificar("Q3 2026 Earnings Results") == ("resultado", "3Q26")


def test_meli_site_de_ri_sobre_yahoo(monkeypatch):
    from calendario_b3.__main__ import _aplicar_ri_exterior

    cfg = {"exterior": [{"ticker": "MELI", "nome": "MercadoLibre", "ri": ["https://investor"]}]}
    hoje = date(2026, 10, 9)
    site = lambda provisorio, dia=date(2026, 11, 4): monkeypatch.setattr(ri, "coletar", lambda itens, h: (
        [ri.EventoRI("MELI", "resultado", "3Q26", dia, provisorio=provisorio)], set()))
    yahoo_ok = [Evento("MELI", "MercadoLibre", "Resultado 3Q26", date(2026, 11, 4), time(17))]
    yahoo_est = [Evento("MELI", "MercadoLibre", "Resultado 3Q26 (estimado)", date(2026, 11, 5))]
    # Site provisório, Yahoo confirmado: fica o Yahoo (com horário).
    site(True)
    evs, _ = _aplicar_ri_exterior(cfg, yahoo_ok, hoje)
    assert [(e.evento, e.data, e.hora) for e in evs] == [("Resultado 3Q26", date(2026, 11, 4), time(17))]
    # Site confirmado, Yahoo estimado: vale o site e sai o "(estimado)".
    site(False)
    evs, _ = _aplicar_ri_exterior(cfg, yahoo_est, hoje)
    assert [(e.evento, e.data) for e in evs] == [("Resultado 3Q26", date(2026, 11, 4))]
    # Os dois provisórios: data do site, ainda estimada.
    site(True)
    evs, _ = _aplicar_ri_exterior(cfg, yahoo_est, hoje)
    assert [(e.evento, e.data) for e in evs] == [("Resultado 3Q26 (estimado)", date(2026, 11, 4))]
    # Site fora do ar: o call já conhecido é preservado no histórico.
    monkeypatch.setattr(ri, "coletar", lambda itens, h: ([], {"MELI"}))
    assert _aplicar_ri_exterior(cfg, yahoo_ok, hoje) == (yahoo_ok, {("MELI", "call")})


def test_whatsapp_callmebot(monkeypatch, caplog):
    import requests as rq

    assert notificar.destinatarios_whatsapp(" +55 (11) 99999-9999:123456,\n5521988887777 : 654321; lixo") == [
        ("+5511999999999", "123456"), ("+5521988887777", "654321")]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CALLMEBOT_WHATSAPP", "+5511999999999:111111, +5521988887777:222222")
    enviados = []

    class Resp:
        status_code, text = 200, "<p>Message queued. You will receive it in a few seconds.</p>"

    monkeypatch.setattr(notificar.requests, "get", lambda url, params, timeout: enviados.append(params) or Resp())
    assert notificar.enviar(["📅 MGLU Resultado 3Q26: 05/11 → 09/11"], pausa=0) is True
    assert [p["phone"] for p in enviados] == ["+5511999999999", "+5521988887777"]
    assert enviados[0]["text"] == "*Calendário de resultados*\n\n📅 MGLU Resultado 3Q26: 05/11 → 09/11"

    # Falha: nem telefone nem apikey aparecem no log (repositório público).
    def falha(url, params, timeout):
        raise rq.ConnectionError(f"Max retries exceeded with url: /whatsapp.php?phone=%2B5511999999999&apikey={params['apikey']}")

    monkeypatch.setattr(notificar.requests, "get", falha)
    caplog.clear()
    assert notificar.enviar(["linha"], pausa=0) is False
    assert "111111" not in caplog.text and "5511999999999" not in caplog.text and "falha ao enviar" in caplog.text

    class Invalida:
        status_code, text = 200, "APIKey is invalid. Please check it."

    monkeypatch.setattr(notificar.requests, "get", lambda url, params, timeout: Invalida())
    assert notificar.enviar(["linha"], pausa=0) is False


def test_whatsapp_quebra_mensagem_longa():
    blocos = notificar._blocos("\n".join(f"linha {i:03d} " + "x" * 90 for i in range(40)), 1500)
    assert len(blocos) > 1 and all(len(b) <= 1500 for b in blocos)
    assert "\n".join(blocos).count("linha") == 40


# Texto real do PDF "Sea Limited to Report Second Quarter 2026 Results" (28/07/2026).
COMUNICADO_SEA = """Sea Limited to Report Second Quarter 2026 Results
Singapore, July 28, 2026 – Sea Limited (NYSE: SE) (“Sea” or the “Company”) plans to announce
its second quarter 2026 results before the U.S. market opens on August 11, 2026, U.S. Eastern
Time.
The Company’s management will host a conference call to discuss the second quarter 2026 results.
Details of the webcast are as follows:
Date and time: 7:30 AM U.S. Eastern Time on August 11, 2026
7:30 PM Singapore / Hong Kong Time on August 11, 2026
Webcast link: https://events.q4inc.com/attendee/265654308
"""


def test_comunicado_sea():
    evs = ri.de_comunicado("SE", COMUNICADO_SEA)
    # Agosto: ET = UTC-4, então 7:30 ET = 08:30 em Brasília.
    assert [(e.tipo, e.rotulo, e.dia, e.inicio, e.link) for e in evs] == [
        ("resultado", "2Q26", date(2026, 8, 11), None, ""),
        ("call", "2Q26", date(2026, 8, 11), time(8, 30), "https://events.q4inc.com/attendee/265654308"),
    ]


def test_sea_api_de_noticias(monkeypatch):
    # Trecho real de sea.com/api/invest/news (mais recentes primeiro; um link vem com "\r\n").
    dados = {"data": [
        {"title": "Sea Limited Reports Second Quarter 2026 Results", "publish_date": "2026/08/11",
         "document_url": "https://cdn.sea.com/investor/2Q2026/wFBC39MbqnfLb3MGY6LP/2026.08.11%20Sea%20Second%20Quarter%202026%20Results.pdf"},
        {"title": "Sea Limited to Report Second Quarter 2026 Results", "publish_date": "2026/07/28",
         "document_url": "https://cdn.sea.com/investor/2Q2026/FBWWpVy43Auf1EnpVt6o/2026.07.28%20Sea%20Limited%20to%20Report%20Second%20Quarter%202026%20Results.pdf"},
        {"title": "Sea Limited to Report First Quarter 2026 Results", "publish_date": "2026/04/28",
         "document_url": "https://cdn.sea.com/investor/1Q2026/DpQXsm6JO7M8u8NysfCh/2026.04.28%20Sea%20Limited%20to%20Report%20First%20Quarter%202026%20Results.pdf\r\n"},
    ]}
    abertos = []

    class Pagina:
        def __init__(self, texto):
            self.texto = texto

        def extract_text(self):
            return self.texto

    monkeypatch.setattr(ri, "_get", lambda url: abertos.append(url) or type("R", (), {"content": b"%PDF"})())
    import pypdf
    monkeypatch.setattr(pypdf, "PdfReader", lambda f: type("P", (), {"pages": [Pagina(COMUNICADO_SEA)]})())
    evs = ri.de_noticias_pdf("SE", dados)
    assert len(abertos) == 1 and "2026.07.28" in abertos[0]
    assert [(e.tipo, e.dia) for e in evs] == [("resultado", date(2026, 8, 11)), ("call", date(2026, 8, 11))]


# Texto real do PDF "MercadoLibre, Inc. to Report Second Quarter 2026 Financial Results" (29/07/2026).
COMUNICADO_MELI = """MercadoLibre, Inc. to Report Second Quarter 2026 Financial Results
MONTEVIDEO, Uruguay; July 29, 2026; (GLOBE NEWSWIRE) -- MercadoLibre, Inc. (Nasdaq: MELI)
( http://www.mercadolibre.com ) announces that it intends to release financial results for its second
fiscal quarter ending June 30, 2026, on August 5, 2026. The Company will host its earnings results
video conference, as well as a conference call and audio webcast, on August 5, at 5:00 p.m. Eastern
Time. In order to access our video webcast and the live audio, investors, analysts and the market in
general may access the following link at https://event.choruscall.com/hmediaframe/webcast.html?webcastid=xr1sGkCu
to attend the live event. To participate in our conference call Q&A, investors, analysts and the market
in general may access the following link https://hdr.choruscall.com/?$Y2FsbHR5cGU9MiZyPXRydWUmaW5mbz1jb21wYW55LXBob25l
"""


def test_comunicado_meli():
    evs = ri.de_comunicado("MELI", COMUNICADO_MELI)
    # Agosto: ET = UTC-4, então 17:00 ET = 18:00 em Brasília.
    assert [(e.tipo, e.rotulo, e.dia, e.inicio, e.link) for e in evs] == [
        ("resultado", "2Q26", date(2026, 8, 5), None, ""),
        ("call", "2Q26", date(2026, 8, 5), time(18), "https://event.choruscall.com/hmediaframe/webcast.html?webcastid=xr1sGkCu"),
    ]
    # Página de eventos da MELI: o comunicado mais recente é escolhido pelo trimestre no nome do arquivo.
    base = "https:\\u002F\\u002Fhttp2.mlstatic.com\\u002Fstorage\\u002Fx\\u002F"
    html = (f'"url":"{base}MELI_to_Report_First_Quarter_2026_Financial_Results.pdf"'
            f'"url":"{base}MELI_to_Report_Second_Quarter_2026_Financial_Results.docx.pdf"'
            f'"url":"{base}mercadolibre-inc-report-fourth-quarter-2025-financial-results.pdf"')
    assert ri.comunicados_pdf(html) == [
        "https://http2.mlstatic.com/storage/x/MELI_to_Report_Second_Quarter_2026_Financial_Results.docx.pdf",
        "https://http2.mlstatic.com/storage/x/MELI_to_Report_First_Quarter_2026_Financial_Results.pdf",
    ]


def test_meli_comunicado_prevalece_sobre_agenda_provisoria(monkeypatch):
    html = MELI_EVENTOS + '"url":"https://h/MELI_to_Report_Third_Quarter_2026_Financial_Results.pdf"'
    texto = COMUNICADO_MELI.replace("Second Quarter", "Third Quarter").replace("August 5", "November 4")
    monkeypatch.setattr(ri, "_get", lambda url: type("R", (), {"text": html, "content": b"%PDF"})())
    monkeypatch.setattr(ri, "eventos_de_comunicado", lambda t, bruto, fuso="America/Sao_Paulo": ri.de_comunicado(t, texto))
    evs = ri.eventos_da_empresa({"ticker": "MELI", "ri": ["https://investor"]}, date(2026, 10, 9))
    # Novembro: ET = UTC-5, então 17:00 ET = 19:00 em Brasília.
    assert [(e.tipo, e.rotulo, e.dia, e.inicio, e.provisorio) for e in evs] == [
        ("resultado", "3Q26", date(2026, 11, 4), None, False), ("call", "3Q26", date(2026, 11, 4), time(19), False)]


# Trecho real do pop-up da home do Grupo SBF (13/10/2026); a agenda da MZ traz o call sem horário.
POPUP_SBFG = """<div id="popup"><div class="box-msg"><div class="info"><i class="close">x</i>
<header><h2>DIVULGAÇÃO DE RESULTADOS 3T26</h2></header><main>
<p><strong>Segunda-feira, 09 de Novembro de 2026</strong><br />
Após o fechamento do mercado.</p><h2></h2><p>&nbsp;</p>
<h2><span style="font-family: Mona Sans;font-weight: 600">VIDEOCONFERÊNCIA EM PORTUGUÊS</span></h2>
<p>(Com tradução simultânea para o inglês)</p>
<p><strong>Terça-feira, 10 de Novembro de 2026<br />
</strong>11h (Brasília) / 09h (NY) / 14h (Londres)</p>
<p><a href="https://mzgroup.zoom.us/webinar/register/WN_2hTIaG29S7yFDIKVCSRnZw#/registration" target="_blank" rel="noopener">Clique aqui</a> para acessar a videoconferência.</p>
<main></div></div></div>"""


def test_popup_sbfg_horario_e_link(monkeypatch):
    monkeypatch.setattr(ri, "_get", lambda url: type("R", (), {"text": POPUP_SBFG})())
    monkeypatch.setattr(ri, "mz_id", lambda html: "id")
    monkeypatch.setattr(ri, "mz_eventos", lambda fm, tipo: MZ_SBFG)
    # 28 dias antes do call: o link vale porque o pop-up cita a data do call.
    evs = ri.eventos_da_empresa({"ticker": "SBFG3", "ri": ["https://ri"]}, date(2026, 10, 13))
    assert [(e.tipo, e.dia, e.inicio, e.link) for e in evs] == [
        ("resultado", date(2026, 11, 9), None, ""),
        ("call", date(2026, 11, 10), time(11), "https://mzgroup.zoom.us/webinar/register/WN_2hTIaG29S7yFDIKVCSRnZw#/registration"),
    ]


def test_link_generico_da_mz_nao_e_do_call():
    # Rodapé "Powered by MZ" (Renner) não é link do call; o Zoom da MZ é.
    assert ri.links_de_call('<a href="https://www.mzgroup.com/">MZ</a>') == []
    assert ri.links_de_call('<a href="https://mzgroup.zoom.us/webinar/register/WN_x">x</a>') == ["https://mzgroup.zoom.us/webinar/register/WN_x"]


def test_popup_em_imagem_lido_por_ocr(monkeypatch):
    # Pop-up real da C&A (13/10/2026): só uma imagem; o texto do OCR abaixo é ilustrativo.
    home = ('<div id="popup-img" class="popup" role="dialog"><div class="content-info">'
            '<img src="https://cdn-sites-assets.mziq.com/wp-content/uploads/sites/329/2024/04/Agenda_3T26_PT_3.png" alt="">'
            '</div></div><img src="https://x/logo.png">')
    assert ri.imagens_de_popup(home) == ["https://cdn-sites-assets.mziq.com/wp-content/uploads/sites/329/2024/04/Agenda_3T26_PT_3.png"]
    mz = [{"event_name": "Divulgação de Resultados 3T26", "event_date": "2026-11-04T12:00:00.000Z", "event_starttime": ""},
          {"event_name": "Teleconferência de Resultados 3T26", "event_date": "2026-11-05T12:00:00.000Z", "event_starttime": ""}]
    monkeypatch.setattr(ri, "_get", lambda url: type("R", (), {"text": home})())
    monkeypatch.setattr(ri, "mz_id", lambda html: "id")
    monkeypatch.setattr(ri, "mz_eventos", lambda fm, tipo: mz)
    monkeypatch.setattr(ri, "ocr", lambda url: "AGENDA 3T26 Divulgação de Resultados 04/11 após o fechamento "
                                               "Teleconferência de Resultados 05/11 às 11h00 (Brasília)")
    evs = ri.eventos_da_empresa({"ticker": "CEAB3", "ri": ["https://ri"]}, date(2026, 10, 13))
    assert [(e.tipo, e.dia, e.inicio) for e in evs] == [("resultado", date(2026, 11, 4), None), ("call", date(2026, 11, 5), time(11))]


def test_ocr_real_cea_e_panvel():
    # Texto real do OCR (tesseract) dos pop-ups em imagem, 13/10/2026.
    cea = ("Resultados 3126 04 de novembro de 2026 Após o fechamento do mercado Videoconferência 05 de novembro de 2026 "
           "11:00 (BRT) | 09:00 (NY) | 14:00 (LON) Periodo de Siléncio 15 de outubro a 04 de novembro ")
    panvel = ("Participe da teleconferência do Grupo Panvel (B3: PNVL3) referente aos resultados do 3726: 06 de novembro "
              "de 2026 (sexta-feira) 9:30 (Horário de Brasília - BRT) 8:30 (Us EDT) Para acompanhar, clique aqui.")
    assert ri.horarios_no_texto(cea) == {(11, 5): time(11)}
    assert ri.horarios_no_texto(panvel) == {(11, 6): time(9, 30)}


def test_uid_muda_quando_call_ganha_horario():
    # Mesmo UID para o call "dia inteiro" e com horário fazia o Outlook manter 24h de duração.
    sem_hora = Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5))
    com_hora = Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5), time(11))
    outra_hora = Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5), time(10))
    assert ics.uid(sem_hora) == sem_hora.chave
    assert len({ics.uid(sem_hora), ics.uid(com_hora), ics.uid(outra_hora)}) == 3
    assert ics.uid(com_hora) == ics.uid(Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5), time(11)))


# --------------------------------------------------------------------------- convites por e-mail
from calendario_b3 import convites


class SmtpFalso:
    def __init__(self):
        self.enviadas, self.login_ok = [], None

    def login(self, usuario, senha):
        self.login_ok = (usuario, senha)

    def send_message(self, msg):
        self.enviadas.append(msg)

    def quit(self):
        pass


def _cal(msg):
    texto = next(p for p in msg.walk() if p.get_content_type() == "text/calendar").get_content()
    return re.sub(r"\r?\n ", "", texto)  # desfaz a quebra de linhas longas (RFC 5545)


def test_convites_novo_atualizacao_e_cancelamento(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("CONVITE_EMAIL", "calendario@gmail.com")
    monkeypatch.setenv("CONVITE_SENHA", "abcd efgh ijkl mnop")
    monkeypatch.setenv("CONVITE_PARA", "eu@empresa.com, colega@empresa.com")
    estado, hoje, smtp = tmp_path / "convites.json", date(2026, 10, 14), SmtpFalso()
    enviar = lambda evs: convites.enviar(evs, estado, hoje, "America/Sao_Paulo", 60, smtp_factory=lambda: smtp, pausa=0)
    call = Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5))
    passado = Evento("CEAB3", "CEA", "Resultado 2Q26", date(2026, 8, 4))

    assert enviar([passado, call]) == 1  # só o futuro
    msg = smtp.enviadas[0]
    assert msg["Subject"] == "CEAB Call 3Q26 (05/11)" and msg["To"] == "eu@empresa.com, colega@empresa.com"
    cal = _cal(msg)
    assert "METHOD:REQUEST" in cal and "SEQUENCE:0" in cal and "DTSTART;VALUE=DATE:20261105" in cal
    assert "ATTENDEE;ROLE=OPT-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=FALSE:mailto:colega@empresa.com" in cal
    assert smtp.login_ok == ("calendario@gmail.com", "abcdefghijklmnop")
    uid = re.search(r"UID:(\S+)", cal)[1]

    assert enviar([call]) == 0  # nada mudou: nada reenviado

    # Ganhou horário e link: atualização do mesmo convite (mesmo UID, SEQUENCE maior).
    com_hora = Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5), time(11), extras={"link": "https://zoom.us/webinar/register/1"})
    assert enviar([com_hora]) == 1
    cal = _cal(smtp.enviadas[-1])
    assert smtp.enviadas[-1]["Subject"] == "Atualizado: CEAB Call 3Q26 (05/11 11:00)"
    assert f"UID:{uid}" in cal and "SEQUENCE:1" in cal and "DTSTART:20261105T140000Z" in cal and "URL:https://zoom.us/webinar/register/1" in cal

    # Saiu do calendário antes de acontecer: cancelamento.
    assert enviar([]) == 1
    cal = _cal(smtp.enviadas[-1])
    assert smtp.enviadas[-1]["Subject"] == "Cancelado: CEAB Call 3Q26" and "METHOD:CANCEL" in cal and "SEQUENCE:2" in cal and f"UID:{uid}" in cal
    assert json.loads(estado.read_text()) == {}
    assert "abcd" not in caplog.text


def test_convites_sem_configuracao_nao_envia(monkeypatch, tmp_path):
    for v in ("CONVITE_EMAIL", "CONVITE_SENHA", "CONVITE_PARA"):
        monkeypatch.delenv(v, raising=False)
    assert convites.enviar([Evento("X3", "X", "Call 3Q26", date(2026, 11, 5))], tmp_path / "c.json", date(2026, 10, 14), "America/Sao_Paulo", 60) == 0


def test_horario_do_pregao_acompanha_horario_de_verao_americano():
    sp = "America/Sao_Paulo"
    assert convites.horario_pregao("B3", "fechamento", date(2026, 8, 5), sp) == time(17)   # EUA no horário de verão
    assert convites.horario_pregao("B3", "fechamento", date(2026, 11, 5), sp) == time(18)  # depois de 01/11
    assert convites.horario_pregao("NASDAQ", "fechamento", date(2026, 11, 4), sp) == time(18)
    assert convites.horario_pregao("NYSE", "abertura", date(2026, 11, 10), sp) == time(11, 30)


def test_convite_divulgacao_no_fechamento_e_lembretes():
    agora = datetime(2026, 10, 14, tzinfo=timezone.utc)
    monta = lambda e, mercado=None: re.sub(r"\r?\n ", "", convites.montar(
        "REQUEST", "k", 0, e, "a@gmail.com", ["b@x.com"], "America/Sao_Paulo", 60, agora, mercado))
    # Divulgação B3 em 05/11: 18:00-19:00 de Brasília (21:00-22:00 UTC), lembrete 15 min.
    cal = monta(Evento("CEAB3", "CEA", "Resultado 3Q26", date(2026, 11, 5)))
    assert "DTSTART:20261105T210000Z" in cal and "DTEND:20261105T220000Z" in cal and "TRIGGER:-PT15M" in cal
    # Sea divulga antes da abertura da NYSE: 9:30 ET = 11:30 de Brasília em novembro.
    cal = monta(Evento("SE", "Sea", "Resultado 3Q26", date(2026, 11, 10)), {"SE": ("NYSE", "abertura")})
    assert "DTSTART:20261110T143000Z" in cal and "DTEND:20261110T153000Z" in cal
    # Call com horário: lembrete 30 min; sem horário (dia inteiro): sem lembrete.
    assert "TRIGGER:-PT30M" in monta(Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5), time(11)))
    assert "VALARM" not in monta(Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5)))
    assert cal.index("BEGIN:VALARM") < cal.index("END:VEVENT")


def test_convites_de_teste_resultado_e_call(monkeypatch):
    monkeypatch.setenv("CONVITE_EMAIL", "calendario@gmail.com")
    monkeypatch.setenv("CONVITE_SENHA", "abcdefghijklmnop")
    monkeypatch.setenv("CONVITE_PARA", "eu@empresa.com")
    smtp = SmtpFalso()
    assert convites.teste(smtp_factory=lambda: smtp)
    resultado, call = (_cal(m) for m in smtp.enviadas)
    assert "SUMMARY:TESTE Resultado 3Q26" in resultado and "TRIGGER:-PT15M" in resultado and "DTSTART;VALUE=DATE" not in resultado
    assert "SUMMARY:TESTE Call 3Q26" in call and "TRIGGER:-PT30M" in call and "URL:https://zoom.us/webinar/register/WN_TESTE_ficticio" in call
    assert smtp.enviadas[1]["Subject"].startswith("🧪 Teste: TESTE Call 3Q26 (") and smtp.enviadas[1]["Subject"].endswith(" 11:00)")


def test_convites_varios_na_mesma_rodada(monkeypatch, tmp_path):
    # Bug real: "time" (datetime.time) sombreava o módulo time e o 2º envio em diante falhava.
    monkeypatch.setenv("CONVITE_EMAIL", "calendario@gmail.com")
    monkeypatch.setenv("CONVITE_SENHA", "abcdefghijklmnop")
    monkeypatch.setenv("CONVITE_PARA", "eu@empresa.com")
    smtp = SmtpFalso()
    evs = [Evento("RADL3", "RD", "Resultado 3Q26", date(2026, 11, 3)), Evento("RADL3", "RD", "Call 3Q26", date(2026, 11, 4), time(10)),
           Evento("CEAB3", "CEA", "Resultado 3Q26", date(2026, 11, 4))]
    assert convites.enviar(evs, tmp_path / "c.json", date(2026, 10, 14), "America/Sao_Paulo", 60, smtp_factory=lambda: smtp, pausa=0) == 3
    assert [m["Subject"] for m in smtp.enviadas] == [
        "RADL Resultado 3Q26 (03/11 18:00)", "CEAB Resultado 3Q26 (04/11 18:00)", "RADL Call 3Q26 (04/11 10:00)"]


def test_convite_com_horario_fica_ocupado():
    agora = datetime(2026, 10, 14, tzinfo=timezone.utc)
    monta = lambda e: convites.montar("REQUEST", "k", 0, e, "a@gmail.com", ["b@x.com"], "America/Sao_Paulo", 60, agora)
    for e in (Evento("CEAB3", "CEA", "Resultado 3Q26", date(2026, 11, 4)), Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5), time(11))):
        cal = monta(e)
        assert "TRANSP:OPAQUE" in cal and "X-MICROSOFT-CDO-BUSYSTATUS:BUSY" in cal and "INTENDEDSTATUS:BUSY" in cal and "FREE" not in cal
    cal = monta(Evento("CEAB3", "CEA", "Call 3Q26", date(2026, 11, 5)))  # call ainda sem horário: dia inteiro, livre
    assert "TRANSP:TRANSPARENT" in cal and "INTENDEDSTATUS:FREE" in cal and "STATUS:BUSY" not in cal


def test_convites_quem_entra_na_lista_recebe_os_ja_enviados(monkeypatch, tmp_path):
    monkeypatch.setenv("CONVITE_EMAIL", "calendario@gmail.com")
    monkeypatch.setenv("CONVITE_SENHA", "abcdefghijklmnop")
    estado, hoje, smtp = tmp_path / "convites.json", date(2026, 10, 14), SmtpFalso()
    enviar = lambda evs: convites.enviar(evs, estado, hoje, "America/Sao_Paulo", 60, smtp_factory=lambda: smtp, pausa=0)
    call = Evento("PNVL3", "DIMED", "Call 3Q26", date(2026, 11, 6), time(9, 30))
    monkeypatch.setenv("CONVITE_PARA", "eu@empresa.com")
    assert enviar([call]) == 1
    # Entraram duas pessoas: só elas recebem, mesmo UID e mesma SEQUENCE; nada sai para quem já tinha.
    monkeypatch.setenv("CONVITE_PARA", "eu@empresa.com, ana@empresa.com, bia@empresa.com")
    assert enviar([call]) == 1
    msg = smtp.enviadas[-1]
    assert msg["To"] == "ana@empresa.com, bia@empresa.com" and msg["Subject"] == "PNVL Call 3Q26 (06/11 09:30)"
    assert "SEQUENCE:0" in _cal(msg) and _cal(msg).count("ATTENDEE") == 3
    assert enviar([call]) == 0
    # O estado (repositório público) guarda só hashes dos e-mails.
    assert "empresa.com" not in estado.read_text()
    # Mudou o horário: atualização para todos.
    assert enviar([Evento("PNVL3", "DIMED", "Call 3Q26", date(2026, 11, 6), time(10))]) == 1
    assert smtp.enviadas[-1]["To"] == "eu@empresa.com, ana@empresa.com, bia@empresa.com" and "SEQUENCE:1" in _cal(smtp.enviadas[-1])


def test_call_manual_com_horario_e_link():
    from calendario_b3 import ajustes

    item = {"ticker": "RIAA3", "nomes": ["RIACHUELO"], "manual": {
        "call 3Q26": {"quando": "2026-11-10 10:00", "link": "https://mzgroup.zoom.us/webinar/register/WN_x#/registration"},
        "3Q26": "2026-11-09"}}
    call, resultado = ajustes.manuais([item])
    assert (call.evento, call.data, call.hora, call.extras["link"]) == (
        "Call 3Q26", date(2026, 11, 10), time(10), "https://mzgroup.zoom.us/webinar/register/WN_x#/registration")
    assert (resultado.evento, resultado.data, resultado.hora) == ("Resultado 3Q26", date(2026, 11, 9), None)
    # Substitui o call que veio do RI/CVM (mesmo ticker, tipo e trimestre).
    do_ri = Evento("RIAA3", "RIACHUELO", "Call 3Q26", date(2026, 11, 10), extras={"fonte": "RI"})
    assert ajustes.substituir([do_ri], [call]) == [call]


def test_link_do_call_em_portugues_primeiro():
    # Dois calls na mesma home: o link em português vem primeiro, mesmo que o em inglês apareça antes.
    html = ('<h2>VIDEOCONFERÊNCIA EM INGLÊS</h2><p>13 de Novembro 12h</p>'
            '<a href="https://mzgroup.zoom.us/webinar/register/WN_EN#/registration">Click here</a>'
            '<h2>VIDEOCONFERÊNCIA EM PORTUGUÊS</h2><p>(Com tradução simultânea para o inglês)</p><p>13 de Novembro 10h</p>'
            '<a href="https://mzgroup.zoom.us/webinar/register/WN_PT#/registration">Clique aqui</a>')
    assert ri.links_de_call(html) == ["https://mzgroup.zoom.us/webinar/register/WN_PT#/registration",
                                      "https://mzgroup.zoom.us/webinar/register/WN_EN#/registration"]
    # "tradução simultânea para o inglês" não faz o link virar "em inglês" (pop-up real do Grupo SBF).
    assert ri.links_de_call(POPUP_SBFG) == ["https://mzgroup.zoom.us/webinar/register/WN_2hTIaG29S7yFDIKVCSRnZw#/registration"]


def test_pagina_em_ingles_troca_pela_versao_em_portugues(monkeypatch):
    pedidas = []

    def get(url):
        pedidas.append(url)
        if len(pedidas) == 1:  # 1º acesso redireciona para /en/ (ex.: geolocalização)
            return type("R", (), {"url": "https://ri.x.com.br/en/", "text": '<html lang="en-US">EN'})()
        return type("R", (), {"url": "https://ri.x.com.br/", "text": '<html lang="pt-BR">PT'})()

    monkeypatch.setattr(ri, "_get", get)
    assert ri._pagina_pt("X3", "https://ri.x.com.br/") == '<html lang="pt-BR">PT'
    assert pedidas == ["https://ri.x.com.br/", "https://ri.x.com.br/"]


def test_fechamento_euronext_em_brasilia():
    # Paris fecha 17:30: 12:30 de Brasília no horário de verão europeu, 13:30 no inverno.
    assert convites.horario_pregao("EURONEXT", "fechamento", date(2026, 10, 22), "America/Sao_Paulo") == time(12, 30)
    assert convites.horario_pregao("EURONEXT", "fechamento", date(2027, 2, 24), "America/Sao_Paulo") == time(13, 30)


def test_nome_de_exibicao_no_lugar_do_ticker(monkeypatch):
    monkeypatch.setattr(ics, "NOMES", {"CA": "Carrefour França", "SE": "Sea Ltd"})
    assert ics.titulo(Evento("CA", "Carrefour", "Resultado 3Q26", date(2026, 10, 22))) == "Carrefour França Resultado 3Q26"
    assert ics.titulo(Evento("SE", "Sea", "Call 3Q26", date(2026, 11, 10))) == "Sea Ltd Call 3Q26"
    assert ics.titulo(Evento("LREN3", "R", "Call 3Q26", date(2026, 11, 6))) == "LREN Call 3Q26"
