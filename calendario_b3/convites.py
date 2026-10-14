"""Convites de reunião por e-mail: põe os eventos no calendário principal do Outlook.

O calendário assinado por link não aparece em todas as telas do Outlook (ex.: agenda da Barra de
Tarefas Pendentes, app no celular). O convite (iTIP, METHOD:REQUEST) entra no calendário principal.

Cada evento tem UID fixo por empresa, tipo e trimestre ("LREN call 3Q26"); quando data, horário,
título ou link mudam, sai uma atualização do mesmo convite com SEQUENCE maior (o Outlook substitui
o evento), e quando o evento some do calendário sai um cancelamento (METHOD:CANCEL).
`dados/convites.json` guarda o que já foi enviado.

Variáveis de ambiente (secrets do GitHub):
- CONVITE_EMAIL: conta Gmail que envia (organizadora dos convites)
- CONVITE_SENHA: senha de app dessa conta (não a senha normal)
- CONVITE_PARA: destinatários, separados por vírgula
Sem elas, nada é enviado.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import smtplib
import time
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ajustes, ics
from .modelo import Evento

log = logging.getLogger(__name__)

SMTP = ("smtp.gmail.com", 465)


def _chave(e: Evento) -> str:
    t, tipo, rotulo = ajustes.chave(e)
    return f"{t} {tipo} {rotulo or e.evento}"


def _uid(chave: str) -> str:
    return hashlib.sha1(f"convite|{chave}".encode()).hexdigest()[:20] + "@retail-calendar"


def _corpo(e: Evento, fuso: str, duracao: int) -> list[str]:
    return [*ics.horario(e, ZoneInfo(fuso), duracao), *ics.conteudo(e)]


def _assinatura(e: Evento, fuso: str, duracao: int) -> str:
    return hashlib.sha1("\n".join(_corpo(e, fuso, duracao)).encode()).hexdigest()[:16]


def montar(metodo: str, chave: str, sequencia: int, e: Evento, organizador: str, destinatarios: list[str],
           fuso: str, duracao: int, agora: datetime) -> str:
    """iCalendar de um convite (REQUEST) ou cancelamento (CANCEL)."""
    linhas = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{ics.PRODID}", "CALSCALE:GREGORIAN", f"METHOD:{metodo}",
        "BEGIN:VEVENT",
        f"UID:{_uid(chave)}",
        f"SEQUENCE:{sequencia}",
        f"DTSTAMP:{agora.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"ORGANIZER;CN=Calendário de Resultados:mailto:{organizador}",
        *[f"ATTENDEE;ROLE=OPT-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=FALSE:mailto:{d}" for d in destinatarios],
        *_corpo(e, fuso, duracao),
        "X-MICROSOFT-CDO-INTENDEDSTATUS:FREE",
        f"STATUS:{'CANCELLED' if metodo == 'CANCEL' else 'CONFIRMED'}",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(ics._dobrar(l) for l in linhas) + "\r\n"


def _quando(e: Evento) -> str:
    return f"{e.data:%d/%m}" + (f" {e.hora:%H:%M}" if e.hora else "")


def _mensagem(metodo: str, assunto: str, texto: str, cal: str, de: str, para: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = assunto, de, ", ".join(para)
    msg.set_content(texto)
    # Outlook reconhece o convite pela parte text/calendar com o mesmo "method" do iCalendar.
    msg.add_alternative(cal, subtype="calendar", params={"method": metodo, "charset": "UTF-8"})
    msg.add_attachment(cal.encode("utf-8"), maintype="application", subtype="ics", filename="convite.ics")
    return msg


def planejar(eventos: list[Evento], estado: dict, hoje: date, fuso: str, duracao: int) -> list[tuple]:
    """O que enviar: [(metodo, chave, sequencia, evento, assinatura), ...]."""
    futuros = {_chave(e): e for e in eventos if e.data >= hoje}
    plano = []
    for chave, e in sorted(futuros.items(), key=lambda kv: (kv[1].data, kv[0])):
        assinatura = _assinatura(e, fuso, duracao)
        anterior = estado.get(chave)
        if anterior is None:
            plano.append(("REQUEST", chave, 0, e, assinatura))
        elif anterior["assinatura"] != assinatura:
            plano.append(("REQUEST", chave, anterior["sequencia"] + 1, e, assinatura))
    for chave, anterior in estado.items():
        if chave not in futuros and anterior["data"] >= hoje.isoformat():  # sumiu antes de acontecer
            e = Evento.de_json(anterior["evento"])
            plano.append(("CANCEL", chave, anterior["sequencia"] + 1, e, anterior["assinatura"]))
    return plano


def enviar(eventos: list[Evento], caminho_estado: Path, hoje: date, fuso: str, duracao: int,
           smtp_factory=None, pausa: float = 1.0) -> int:
    """Envia convites novos, atualizações e cancelamentos. Devolve quantos e-mails saíram."""
    de, senha = os.environ.get("CONVITE_EMAIL", "").strip(), os.environ.get("CONVITE_SENHA", "").replace(" ", "")
    para = [d.strip() for d in re.split(r"[,;\s]+", os.environ.get("CONVITE_PARA", "")) if d.strip()]
    if not (de and senha and para):
        log.info("CONVITE_EMAIL/CONVITE_SENHA/CONVITE_PARA não configurados; convites não enviados")
        return 0
    estado = json.loads(caminho_estado.read_text(encoding="utf-8")) if caminho_estado.exists() else {}
    plano = planejar(eventos, estado, hoje, fuso, duracao)
    # Eventos que já passaram saem do estado (não há o que atualizar).
    estado = {k: v for k, v in estado.items() if v["data"] >= hoje.isoformat()}
    if not plano:
        caminho_estado.write_text(json.dumps(estado, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    enviados = 0
    agora = datetime.now(timezone.utc)
    try:
        conexao = (smtp_factory or (lambda: smtplib.SMTP_SSL(*SMTP, timeout=60)))()
        conexao.login(de, senha)
    except Exception as e:
        # A senha nunca vai para o log (repositório público).
        log.error("convites: falha ao conectar no Gmail (%s)", type(e).__name__)
        return 0
    try:
        for i, (metodo, chave, seq, e, assinatura) in enumerate(plano):
            nome = ics.titulo(e)
            if metodo == "CANCEL":
                assunto, texto = f"Cancelado: {nome}", f"{nome} ({_quando(e)}) saiu do calendário de resultados."
            elif seq == 0:
                assunto, texto = f"{nome} ({_quando(e)})", f"{nome}: {_quando(e)}" + (f"\nWebcast: {e.extras['link']}" if e.extras.get("link") else "")
            else:
                assunto, texto = f"Atualizado: {nome} ({_quando(e)})", f"{nome}: {_quando(e)}" + (f"\nWebcast: {e.extras['link']}" if e.extras.get("link") else "")
            cal = montar(metodo, chave, seq, e, de, para, fuso, duracao, agora)
            try:
                if i:
                    time.sleep(pausa)
                conexao.send_message(_mensagem(metodo, assunto, texto, cal, de, para))
            except Exception as erro:
                log.error("convites: falha ao enviar %s (%s); nova tentativa na próxima rodada", assunto, type(erro).__name__)
                continue
            enviados += 1
            log.info("convite enviado: %s", assunto)
            if metodo == "CANCEL":
                estado.pop(chave, None)
            else:
                estado[chave] = {"sequencia": seq, "assinatura": assinatura, "data": e.data.isoformat(), "evento": e.para_json()}
    finally:
        try:
            conexao.quit()
        except Exception:
            pass
    caminho_estado.write_text(json.dumps(estado, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return enviados


def teste(fuso: str = "America/Sao_Paulo") -> bool:
    """Um convite de teste para amanhã às 12:00 (para conferir se chega ao calendário do Outlook)."""
    from datetime import time as hora, timedelta

    de, senha = os.environ.get("CONVITE_EMAIL", "").strip(), os.environ.get("CONVITE_SENHA", "").replace(" ", "")
    para = [d.strip() for d in re.split(r"[,;\s]+", os.environ.get("CONVITE_PARA", "")) if d.strip()]
    if not (de and senha and para):
        log.info("CONVITE_EMAIL/CONVITE_SENHA/CONVITE_PARA não configurados; convite de teste não enviado")
        return False
    amanha = datetime.now(ZoneInfo(fuso)).date() + timedelta(days=1)
    e = Evento("TESTE", "Teste", "Convite de teste (pode apagar)", amanha, hora(12))
    agora = datetime.now(timezone.utc)
    cal = montar("REQUEST", f"teste {agora:%Y%m%d%H%M%S}", 0, e, de, para, fuso, 30, agora)
    try:
        with smtplib.SMTP_SSL(*SMTP, timeout=60) as conexao:
            conexao.login(de, senha)
            conexao.send_message(_mensagem("REQUEST", "🧪 Teste: convite do calendário de resultados", "Se este evento apareceu "
                                           "no seu calendário, os convites estão funcionando. Pode apagar.", cal, de, para))
    except Exception as erro:
        log.error("convite de teste: falha (%s)", type(erro).__name__)
        return False
    log.info("convite de teste enviado para %d destinatário(s)", len(para))
    return True
