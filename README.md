# Calendário de resultados (B3 → Outlook)

Lê o **Cronograma de Eventos Corporativos** da B3
([página](https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/acoes/consultas/cronograma-de-eventos-corporativos/)),
filtra as empresas da cobertura e os eventos de resultado (ITR, DFP, divulgação de
resultados, teleconferência), e gera um calendário `.ics` que o Outlook **assina por link**.
Quando a B3 atualiza ou remarca uma data, o Outlook recebe a mudança sozinho.

```
GitHub Actions (4x/dia)
  ├─ baixa a página + planilha do cronograma da B3
  ├─ filtra cobertura (config.yaml) + eventos de resultado
  └─ grava publico/calendario.ics no repo ──► link raw ──► Outlook (assinatura)
```

Link para assinar:

```
https://raw.githubusercontent.com/vncsprttdsz/retail-calendar/main/publico/calendario.ics
```

## Configuração

1. **Cobertura**: `config.yaml` → `cobertura.empresas` (mesmo universo do dashboard
   retail-coverage, só empresas listadas na B3). O ticker casa pela raiz (LREN3 → LREN);
   os `nomes` servem quando a planilha da B3 traz só a razão social.
   Para levar todos os eventos (assembleias, dividendos etc.), use `eventos.incluir_todos: true`.
2. Leve estes arquivos para a branch `main` (agendamentos do GitHub Actions só rodam na branch padrão).
3. **Primeira execução**: *Actions → Atualizar calendário de resultados → Run workflow*
   marcando **inspecionar**. O log mostra quais colunas da planilha da B3 foram reconhecidas
   (empresa, código, evento, data). Se algo vier errado, force os nomes das colunas em
   `config.yaml` → `fonte.colunas`. Depois rode de novo sem marcar a opção: isso cria
   `publico/calendario.ics`.

4. **Empresas listadas fora do Brasil** (ex.: MELI): `config.yaml` → `exterior`. A próxima
   data vem do Yahoo Finance; enquanto não for confirmada, o título leva "(estimado)".
   Quando a empresa anunciar a data (MELI avisa ~1 semana antes), dá para fixá-la em
   `manual`, que prevalece sobre o Yahoo: `manual: { 3Q26: "2026-11-04 18:00" }`
   (horário de Brasília; sem horário = dia inteiro). Se o Yahoo estiver fora do ar,
   o evento já conhecido é mantido.

5. **Data corrigida à mão** (empresas da B3): `manual` na empresa, ex.:
   `- { ticker: MGLU3, ..., manual: { 3Q26: "2026-11-09" } }`. Prevalece sobre B3 e CVM.
   Remova quando a fonte oficial já trouxer a data nova.

### Avisos no Telegram (opcional)

A cada mudança de data (remarcação, confirmação de data estimada, evento novo ou removido)
o workflow manda uma mensagem, ex.: `📅 MGLU Resultado 3Q26: 05/11 → 09/11`.

1. No Telegram, fale com **@BotFather** → `/newbot` → escolha nome e usuário. Ele responde com o
   **token** do bot (algo como `123456:ABC-...`).
2. Abra a conversa com o seu bot e mande qualquer mensagem (ex.: "oi").
3. No navegador, abra `https://api.telegram.org/bot<TOKEN>/getUpdates` e copie o número em
   `"chat":{"id": ...}` — é o **chat id**. (Para avisar um grupo: adicione o bot ao grupo,
   mande uma mensagem lá e use o id do grupo, que começa com `-`.)
4. No GitHub: *Settings → Secrets and variables → Actions → New repository secret*:
   `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`.

Sem os secrets, as mudanças aparecem só no log do workflow. O token nunca é impresso no log.

## De onde vêm as datas

| Prioridade | Fonte | O que traz |
|---|---|---|
| 1 | `manual` no `config.yaml` | correção pontual |
| 2 | **Site de RI** da companhia (`ri` no config) | confere a data de divulgação; traz o **call** (data, horário e link do webcast) |
| 3 | Calendário de Eventos Corporativos reapresentado (**Plantão de Notícias da B3**, no mesmo dia; **CVM** dados abertos, ~1 semana depois) | última versão entregue pela companhia; também a data do call (apresentação pública) |
| 4 | Planilha consolidada da **B3** | base para todas as empresas, inclusive datas já realizadas |
| — | **Yahoo Finance** | empresas listadas fora do Brasil (`exterior`) |

Site de RI × CVM: quando divergem na data de divulgação, vale a informação mais recente —
se a companhia reapresentou o calendário depois de o site passar a mostrar a data, vale a
CVM (site desatualizado); senão, vale o site. Toda mudança vai para o Telegram.

Sites de RI: os da MZ Group (maioria da cobertura) são lidos pela API de eventos da MZ; os da
RIWeb (Magalu, RD), pelo bloco "Calendário de Eventos" da home. Quando a agenda traz o call
sem horário, o horário escrito no texto da home é usado (ex.: Renner: "Videoconferência:
6 de novembro 10h (Brasil)"). Links de inscrição em
destaque na home (pop-up) são associados ao próximo call (até 21 dias). O link do webcast
costuma aparecer poucos dias antes do call; quando aparece, o evento é atualizado e o
Telegram avisa (`🔗`).

No calendário: `LREN Resultado 3Q26` (dia inteiro) e `LREN Call 3Q26` (com horário quando
informado; descrição com o link do webcast).

Nenhum token é obrigatório: o workflow só lê fontes públicas e grava no próprio repo.

## Assinar no Outlook

- **Outlook Web / novo Outlook**: Calendário → *Adicionar calendário* → *Assinar da Web* → cole o link.
- **Outlook clássico (Windows)**: Calendário → *Adicionar Calendário* → *Da Internet...* → cole o link.

Para compartilhar com colegas, basta enviar o mesmo link.

Observações:
- Quem decide quando atualizar a assinatura é o Outlook/Exchange (em geral, algumas horas
  depois da mudança); o script roda 4x ao dia (07:45, 11:45, 15:45 e 19:45).
- Eventos sem horário viram "dia inteiro" e todos ficam como *Livre*, sem bloquear a agenda.
- O repo é público: o código, o `.ics` e o `dados/eventos.json` (tickers da cobertura +
  datas públicas da B3) ficam visíveis.
- Alguns ambientes corporativos bloqueiam calendários da internet. Nesse caso, uma alternativa
  é importar o `.ics` manualmente ou gravar direto no Outlook via Microsoft Graph (precisa de aprovação da TI).

## Como funciona

- `calendario_b3/fonte.py` baixa a página da B3 e encontra o link da planilha do cronograma.
- `calendario_b3/parser.py` lê xlsx/xls/csv/tabelas HTML, acha o cabeçalho e as colunas por
  nome; aceita formato "longo" (coluna de evento + data) e "largo" (uma coluna de data por evento).
- `calendario_b3/filtros.py` aplica a cobertura e as palavras-chave de `config.yaml`.
- `dados/eventos.json` guarda o histórico: eventos passados continuam no calendário mesmo
  depois que a B3 os remove; eventos futuros que sumiram (remarcados/cancelados) saem.
- Se nada for lido da B3 (download falhou ou layout mudou), o calendário **não** é
  sobrescrito e o workflow falha, e o GitHub envia um e-mail avisando.

## Rodar localmente

```bash
pip install -r requirements.txt
python -m calendario_b3 --inspecionar           # ver como a planilha da B3 foi interpretada
python -m calendario_b3                         # gera publico/calendario.ics
python -m calendario_b3 --arquivo planilha.xlsx # usar um arquivo baixado manualmente
pip install pytest && python -m pytest -q
```
