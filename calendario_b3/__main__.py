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

from . import ajustes, cvm, exterior, fonte, historico, ics, noticias, notificar, parser, ri
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
        try:  # reapresentações do mesmo dia (os dados abertos da CVM atrasam ~1 semana)
            extras = noticias.calendarios(list(_cnpjs(cfg)), hoje)
        except Exception as e:
            log.warning("Plantão de Notícias da B3 indisponível (%s); seguindo só com os dados abertos da CVM", e)
            extras = []
        eventos = cvm.coletar(_cnpjs(cfg), nomes, hoje, cache, extras)
    except Exception as e:
        log.warning("calendários da CVM indisponíveis nesta rodada (%s); usando só a B3", e)
        return []
    caminho_cache.parent.mkdir(parents=True, exist_ok=True)
    caminho_cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    por_chave = {ajustes.chave(e): e for e in b3}
    for e in eventos:
        anterior = por_chave.get(ajustes.chave(e))
        if anterior and anterior.data != e.data:
            log.info("CVM diverge da B3: %s %s B3 %s -> CVM %s", e.ticker, e.evento, anterior.data, e.data)
    return eventos


def _agora_iso() -> str:
    return datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")


def _aplicar_ri(cfg: dict, novos: list, antes: list, hoje, caminho_estado: Path) -> list:
    """Data de divulgação conferida no site de RI e calls (horário, link) dos sites de RI.

    Divergência na data de divulgação: vale a informação mais recente. O estado guarda desde
    quando o RI mostra cada data; se a companhia reapresentou o calendário na CVM depois
    disso, a CVM prevalece (site de RI desatualizado); senão, o RI prevalece.
    """
    from .modelo import Evento

    empresas = (cfg.get("cobertura") or {}).get("empresas") or []
    nomes = {str(i["ticker"]).upper(): (i.get("nomes") or [i["ticker"]])[0] for i in empresas}
    try:
        achados, falhas = ri.coletar(empresas, hoje)
    except Exception as e:
        log.warning("sites de RI indisponíveis nesta rodada (%s)", e)
        return novos
    estado = json.loads(caminho_estado.read_text(encoding="utf-8")) if caminho_estado.exists() else {}
    agora = _agora_iso()
    atuais = {ajustes.chave(e): e for e in novos}
    correcoes, calls = [], {}
    for r in achados:
        if r.dia < hoje:
            continue
        if r.tipo == "call":
            if cvm.dentro_do_prazo(r.rotulo, r.dia, folga=10):
                calls.setdefault((r.ticker, r.rotulo), r)
            continue
        if not cvm.dentro_do_prazo(r.rotulo, r.dia):
            log.warning("%s: data do RI fora do prazo legal, ignorada: %s %s", r.ticker, r.rotulo, r.dia)
            continue
        k = f"{r.ticker} resultado {r.rotulo}"
        if estado.get(k, {}).get("data") != r.dia.isoformat():
            estado[k] = {"data": r.dia.isoformat(), "desde": agora}
        atual = atuais.get((r.ticker, "resultado", r.rotulo))
        if atual and atual.data == r.dia:
            continue
        entregue = (atual.extras.get("entregue") or "") if atual else ""
        if entregue and entregue > estado[k]["desde"]:
            log.warning("%s %s: RI mostra %s, mas a CVM tem reapresentação mais nova (%s) com %s; vale a CVM",
                        r.ticker, r.rotulo, r.dia, entregue, atual.data)
            continue
        log.info("%s %s: RI diverge (%s -> %s); vale o RI", r.ticker, r.rotulo, atual.data if atual else "-", r.dia)
        correcoes.append(Evento(r.ticker, nomes.get(r.ticker, r.ticker), f"Resultado {r.rotulo}", r.dia, extras={"fonte": "RI"}))
    for (t, q), r in calls.items():
        extras = {"fonte": "RI"}
        if r.fim:
            extras["fim"] = r.fim.strftime("%H:%M")
        if r.link:
            extras["link"] = r.link
        correcoes.append(Evento(t, nomes.get(t, t), f"Call {q}", r.dia, r.inicio, extras=extras))
    # RI fora do ar: mantém o call já conhecido (com horário/link) em vez do da CVM, sem horário.
    for e in antes:
        if e.ticker in falhas and ajustes.tipo(e) == "call" and e.data >= hoje and e.extras.get("fonte") == "RI":
            correcoes.append(e)
    caminho_estado.parent.mkdir(parents=True, exist_ok=True)
    caminho_estado.write_text(json.dumps(estado, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return ajustes.substituir(novos, correcoes)


def _aplicar_ri_exterior(cfg: dict, externos: list, hoje) -> tuple[list, set]:
    """Site de RI das empresas do exterior (ex.: MELI) sobre o Yahoo.

    Data de divulgação: o site prevalece, salvo quando ele ainda diz "provisória" e o Yahoo já
    tem data confirmada. Data manual prevalece sobre os dois. Calls entram com data (e horário,
    quando o texto informa em ET). Retorna os eventos e o que preservar se o site falhou.
    """
    from .modelo import Evento

    itens = [i for i in cfg.get("exterior") or [] if i.get("ri")]
    if not itens:
        return externos, set()
    nomes = {str(i["ticker"]).upper(): i.get("nome") or i["ticker"] for i in itens}
    manuais = {(str(i["ticker"]).upper(), str(q).upper()) for i in itens for q in (i.get("manual") or {})}
    try:
        achados, falhas = ri.coletar(itens, hoje)
    except Exception as e:
        log.warning("sites de RI do exterior indisponíveis nesta rodada (%s)", e)
        return externos, {(str(i["ticker"]).upper(), "call") for i in itens}
    atuais = {ajustes.chave(e): e for e in externos}
    correcoes = []
    for r in achados:
        if r.dia < hoje or (r.ticker, r.rotulo) in manuais:
            continue
        nome = nomes.get(r.ticker, r.ticker)
        if r.tipo == "call":
            extras = {"fonte": "RI", **({"link": r.link} if r.link else {})}
            sufixo = " (estimado)" if r.provisorio else ""
            correcoes.append(Evento(r.ticker, nome, f"Call {r.rotulo}{sufixo}", r.dia, r.inicio, extras=extras))
            continue
        atual = atuais.get((r.ticker, "resultado", r.rotulo))
        yahoo_confirmado = atual is not None and "(estimado)" not in atual.evento
        if r.provisorio and yahoo_confirmado:
            if atual.data != r.dia:
                log.warning("%s %s: site de RI diz %s (provisória), Yahoo confirma %s; vale o Yahoo",
                            r.ticker, r.rotulo, r.dia, atual.data)
            continue
        hora = atual.hora if atual is not None and atual.data == r.dia and yahoo_confirmado else None
        if atual is None or atual.data != r.dia or (not r.provisorio and not yahoo_confirmado):
            log.info("%s %s: site de RI -> %s%s (Yahoo: %s)", r.ticker, r.rotulo, r.dia,
                     " provisória" if r.provisorio else "", atual.data if atual else "-")
        titulo = f"Resultado {r.rotulo}" + (" (estimado)" if r.provisorio else "")
        correcoes.append(Evento(r.ticker, nome, titulo, r.dia, hora, extras={"fonte": "RI"}))
    return ajustes.substituir(externos, correcoes), {(t, "call") for t in falhas}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=RAIZ / "config.yaml")
    ap.add_argument("--arquivo", type=Path, help="arquivo local (xlsx/xls/csv/html) no lugar do download")
    ap.add_argument("--saida", type=Path, default=RAIZ / "publico" / "calendario.ics")
    ap.add_argument("--historico", type=Path, default=RAIZ / "dados" / "eventos.json")
    ap.add_argument("--inspecionar", action="store_true", help="só mostra a estrutura detectada")
    ap.add_argument("--detalhar", default="", help="com --inspecionar: tickers (vírgula) cujo calendário da CVM é impresso")
    ap.add_argument("--testar-aviso", action="store_true", help="só manda uma mensagem de teste nos canais configurados")
    ap.add_argument("--sondar", default="", help="URLs (vírgula) para diagnóstico: texto de PDF, links e APIs de HTML")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.sondar:
        ri.sondar([u.strip() for u in args.sondar.split(",") if u.strip()])
        return 0
    if args.testar_aviso:
        ok = notificar.enviar(["🧪 Teste: os avisos de mudança de data do calendário de resultados vão chegar aqui."])
        return 0 if ok else 1

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
            detalhar = tuple(t.strip().upper() for t in args.detalhar.split(",") if t.strip())
            nomes_cfg = {str(i["ticker"]).upper(): [*(i.get("nomes") or []), str(i["ticker"])[:4]]
                         for i in (cfg.get("cobertura") or {}).get("empresas") or []}
            cvm.diagnostico(_cnpjs(cfg), datetime.now(ZoneInfo(cfg["calendario"]["fuso"])).date(), detalhar, nomes_cfg)
        except Exception:
            traceback.print_exc(file=sys.stdout)
        try:
            hoje_i = datetime.now(ZoneInfo(cfg["calendario"]["fuso"])).date()
            print("\n== Sites de RI (eventos de resultado e call)")
            achados, falhas_ri_br = ri.coletar((cfg.get("cobertura") or {}).get("empresas") or [], hoje_i)
            for r in achados:
                print(f"  {r.ticker} {r.tipo:9} {r.rotulo} {r.dia:%d/%m} {r.inicio or ''}-{r.fim or ''} {r.link} | {r.titulo}")
            print(f"  falhas: {sorted(falhas_ri_br)}")
            achados, falhas_ri = ri.coletar([i for i in cfg.get("exterior") or [] if i.get("ri")], hoje_i)
            for r in achados:
                print(f"  {r.ticker} {r.tipo:9} {r.rotulo} {r.dia:%d/%m} {r.inicio or ''} {'provisória ' if r.provisorio else ''}{r.link} | {r.titulo}")
            print(f"  falhas (exterior): {sorted(falhas_ri)}")
            # Sites que falharam ou foram pedidos em "detalhar": diagnóstico do HTML.
            todos = ((cfg.get("cobertura") or {}).get("empresas") or []) + (cfg.get("exterior") or [])
            alvo = (falhas_ri | set(falhas_ri_br) | set(detalhar)) - {"RIAA3"}
            ri.diagnostico([i for i in todos if str(i["ticker"]).upper() in alvo and i.get("ri")])
        except Exception:
            traceback.print_exc(file=sys.stdout)
        try:
            raizes = {t[:4] for t in _cnpjs(cfg)}
            noticias.diagnostico(raizes, datetime.now(ZoneInfo(cfg["calendario"]["fuso"])).date())
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

    # Sites de RI: conferem a data de divulgação e trazem o call (horário e link do webcast).
    antes = historico.carregar(args.historico)
    novos = _aplicar_ri(cfg, novos, antes, hoje, args.historico.parent / "ri_estado.json")

    # Datas manuais prevalecem sobre B3 e CVM.
    manuais = ajustes.manuais((cfg.get("cobertura") or {}).get("empresas"))
    for m in manuais:
        log.info("data manual: %s %s em %s", m.ticker, m.evento, m.data)
    novos = ajustes.substituir(novos, manuais)

    externos, falhas = exterior.coletar(cfg.get("exterior"), cfg_cal["fuso"])
    externos, falhas_ri = _aplicar_ri_exterior(cfg, externos, hoje)
    falhas = set(falhas) | falhas_ri
    novos += externos
    tickers = {e.ticker for e in cobertura} | {str(i["ticker"]).upper() for i in cfg.get("exterior") or []}
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
