"""Atualiza o calendário de resultados a partir do cronograma da B3.

Uso:
    python -m calendario_b3                      # baixa da B3 e gera publico/calendario.ics
    python -m calendario_b3 --inspecionar        # mostra como o arquivo da B3 foi interpretado
    python -m calendario_b3 --arquivo x.xlsx     # usa um arquivo local em vez de baixar
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from . import ajustes, cvm, exterior, fonte, historico, ics, notificar, parser
from .filtros import carregar_cobertura, empresa_da_linha, filtrar
from .modelo import LinhaFonte

log = logging.getLogger("calendario_b3")
RAIZ = Path(__file__).resolve().parent.parent


def coletar_tabelas(cfg_fonte: dict, arquivo_local: Path | None) -> list[tuple[str, pd.DataFrame]]:
    if arquivo_local:
        return [(f"{arquivo_local.name}:{aba}", df) for aba, df in parser.tabelas_de_arquivo(arquivo_local.read_bytes(), arquivo_local.name)]

    tabelas: list[tuple[str, pd.DataFrame]] = []
    if cfg_fonte.get("url_arquivo"):
        urls = [cfg_fonte["url_arquivo"]]
    else:
        pagina = fonte.baixar(cfg_fonte["pagina"])
        html = pagina.conteudo.decode("utf-8", errors="replace")
        tabelas += [(f"página:{n}", df) for n, df in parser.tabelas_de_html(html)]
        urls = fonte.links_de_arquivo(html, pagina.url)
        if not urls:
            for src in fonte.iframes(html, pagina.url):
                sub = fonte.baixar(src)
                sub_html = sub.conteudo.decode("utf-8", errors="replace")
                tabelas += [(f"iframe:{n}", df) for n, df in parser.tabelas_de_html(sub_html)]
                urls += fonte.links_de_arquivo(sub_html, sub.url)
        log.info("arquivos encontrados na página: %s", urls or "nenhum")

    for url in urls[:5]:
        doc = fonte.baixar(url)
        try:
            abas = parser.tabelas_de_arquivo(doc.conteudo, doc.nome)
        except Exception as e:  # arquivo em formato inesperado não derruba os demais
            log.warning("não consegui ler %s (%s): %s", doc.nome, url, e)
            continue
        tabelas += [(f"{doc.nome}:{aba}", df) for aba, df in abas]
    return tabelas


def _cnpjs(cfg: dict) -> dict[str, str]:
    return {str(i["ticker"]).upper(): i.get("cnpj", "") for i in (cfg.get("cobertura") or {}).get("empresas") or []}


def _datas_cvm(cfg: dict, b3: list, hoje, caminho_cache: Path) -> list:
    """Eventos futuros pelos calendários da CVM; se a CVM falhar, segue só com a B3."""
    empresas = (cfg.get("cobertura") or {}).get("empresas") or []
    nomes = {str(i["ticker"]).upper(): (i.get("nomes") or [i["ticker"]])[0] for i in empresas}
    try:
        cache = json.loads(caminho_cache.read_text(encoding="utf-8")) if caminho_cache.exists() else {}
        eventos = cvm.coletar(_cnpjs(cfg), nomes, hoje, cache)
    except Exception as e:
        log.warning("calendários da CVM indisponíveis nesta rodada (%s); usando só a B3", e)
        return []
    caminho_cache.parent.mkdir(parents=True, exist_ok=True)
    caminho_cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    por_chave = {(e.ticker, ajustes.rotulo(e)): e for e in b3}
    for e in eventos:
        anterior = por_chave.get((e.ticker, ajustes.rotulo(e)))
        if anterior and anterior.data != e.data:
            log.info("CVM diverge da B3: %s %s B3 %s -> CVM %s", e.ticker, e.evento, anterior.data, e.data)
    return eventos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=RAIZ / "config.yaml")
    ap.add_argument("--arquivo", type=Path, help="arquivo local (xlsx/xls/csv/html) no lugar do download")
    ap.add_argument("--saida", type=Path, default=RAIZ / "publico" / "calendario.ics")
    ap.add_argument("--historico", type=Path, default=RAIZ / "dados" / "eventos.json")
    ap.add_argument("--inspecionar", action="store_true", help="só mostra a estrutura detectada")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    cfg_cal, cfg_fonte, cfg_ev = cfg["calendario"], cfg["fonte"], cfg.get("eventos", {})
    forcadas = cfg_fonte.get("colunas") or {}

    tabelas = coletar_tabelas(cfg_fonte, args.arquivo)

    if args.inspecionar:
        for nome, df in tabelas:
            print(f"\n== {nome} ({df.shape[0]}x{df.shape[1]})")
            try:
                print(parser.descrever(df, forcadas))
            except Exception:  # inspeção mostra o erro de uma aba e segue para as outras
                traceback.print_exc(file=sys.stdout)
        linhas = [l for _, df in tabelas for l in parser.extrair_linhas(df, forcadas)]
        cobertura = carregar_cobertura(cfg.get("cobertura"), args.config.resolve().parent)
        achadas = {}
        for l in linhas:
            emp = empresa_da_linha(l, cobertura)
            if emp:
                achadas.setdefault(emp.ticker, set()).add(l.empresa or l.codigo)
        print("\n== cobertura")
        for emp in cobertura:
            print(f"  {emp.ticker}: {sorted(achadas[emp.ticker]) if emp.ticker in achadas else 'NÃO ENCONTRADA'}")
        externos, falhas = exterior.coletar(cfg.get("exterior"), cfg["calendario"]["fuso"])
        print("\n== exterior (Yahoo Finance + manual)")
        for e in externos:
            print(f"  {e.ticker}: {e.evento} em {e.data} {e.hora or '(dia inteiro)'}")
        if falhas:
            print(f"  falha na consulta: {sorted(falhas)}")
        nomes = sorted({l.empresa or l.codigo for l in linhas})
        print(f"\n== {len(nomes)} empresas no arquivo:\n  " + " | ".join(nomes))
        try:
            cvm.diagnostico(_cnpjs(cfg), datetime.now(ZoneInfo(cfg["calendario"]["fuso"])).date())
        except Exception:
            traceback.print_exc(file=sys.stdout)
        return 0

    linhas: list[LinhaFonte] = []
    for nome, df in tabelas:
        extraidas = parser.extrair_linhas(df, forcadas)
        log.info("%s: %d linhas com data", nome, len(extraidas))
        linhas += extraidas

    if not linhas:
        # Não sobrescreve o calendário: provavelmente a B3 mudou o layout ou o download falhou.
        log.error("nenhum evento lido da B3 (%d tabelas). Rode com --inspecionar.", len(tabelas))
        return 2

    cobertura = carregar_cobertura(cfg.get("cobertura"), args.config.resolve().parent)
    novos = filtrar(linhas, cobertura, cfg_ev.get("palavras_chave", []), bool(cfg_ev.get("incluir_todos")))
    encontrados = {e.ticker for e in novos}
    log.info("%d eventos da cobertura (%d empresas)", len(novos), len(encontrados))
    sem_evento = [emp.ticker for emp in cobertura if emp.ticker not in encontrados]
    if sem_evento:
        log.info("sem eventos no arquivo da B3: %s", ", ".join(sem_evento))

    hoje = datetime.now(ZoneInfo(cfg_cal["fuso"])).date()

    # Calendário reapresentado na CVM chega antes da planilha consolidada da B3.
    novos = ajustes.substituir(novos, _datas_cvm(cfg, novos, hoje, args.historico.parent / "cvm_cache.json"))

    # Datas manuais prevalecem sobre B3 e CVM.
    manuais = ajustes.manuais((cfg.get("cobertura") or {}).get("empresas"))
    for m in manuais:
        log.info("data manual: %s %s em %s", m.ticker, m.evento, m.data)
    novos = ajustes.substituir(novos, manuais)

    externos, falhas = exterior.coletar(cfg.get("exterior"), cfg_cal["fuso"])
    novos += externos
    tickers = {e.ticker for e in cobertura} | {str(i["ticker"]).upper() for i in cfg.get("exterior") or []}
    antes = historico.carregar(args.historico)
    eventos = historico.mesclar(antes, novos, hoje, tickers, preservar=falhas)
    historico.salvar(args.historico, eventos)
    if antes:  # primeira execução não gera aviso de "tudo novo"
        notificar.enviar(notificar.diferencas(antes, eventos, hoje))

    args.saida.parent.mkdir(parents=True, exist_ok=True)
    conteudo = ics.gerar(
        eventos,
        nome=cfg_cal["nome"],
        descricao=cfg_cal.get("descricao", ""),
        fuso=cfg_cal["fuso"],
        duracao_minutos=int(cfg_cal.get("duracao_minutos", 60)),
    )
    args.saida.write_bytes(conteudo.encode("utf-8"))
    log.info("calendário gravado em %s (%d eventos)", args.saida, len(eventos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
