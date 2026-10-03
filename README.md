# Calendário de resultados (B3 → Outlook)

Lê o **Cronograma de Eventos Corporativos** da B3
([página](https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/renda-variavel/acoes/consultas/cronograma-de-eventos-corporativos/)),
filtra as empresas da sua cobertura e os eventos de resultado (ITR, DFP, divulgação de
resultados, teleconferência), e gera um calendário `.ics` que o Outlook **assina por link**.
Quando a B3 atualiza ou remarca uma data, o Outlook recebe a mudança sozinho.

```
GitHub Actions (4x/dia) ──► B3 (página + planilha) ──► filtro cobertura/eventos
                                                           │
                    Outlook (assinatura) ◄── Gist secreto ◄─┴─ publico/calendario.ics
```

## Configuração (uma vez)

1. **Cobertura**: edite `config.yaml` → `cobertura` (o que está lá é só um exemplo de varejo).
   O `ticker` casa pela raiz (LREN3 → LREN); `nomes` servem quando a B3 só traz o nome da empresa.
   Para levar todos os eventos (assembleias, dividendos etc.), use `eventos.incluir_todos: true`.
2. **Gist secreto** (onde o `.ics` fica publicado, já que o repositório é privado):
   em <https://gist.github.com>, crie um gist **secret** com um arquivo chamado
   `calendario.ics` (qualquer conteúdo). O ID é o código no fim da URL do gist.
3. **Token**: em *Settings → Developer settings → Personal access tokens*, crie um token
   fine-grained com permissão de conta **Gists: Read and write** (ou um token classic com escopo `gist`).
4. **Secrets do repositório** (*Settings → Secrets and variables → Actions*):
   `GIST_ID` e `GIST_TOKEN`.
5. Leve estes arquivos para a branch `main` (agendamentos do GitHub Actions só rodam na branch padrão).
6. **Primeira execução**: *Actions → Atualizar calendário de resultados → Run workflow*
   marcando **inspecionar**. O log mostra quais colunas da planilha da B3 foram reconhecidas
   (empresa, código, evento, data). Se algo vier errado, force os nomes das colunas em
   `config.yaml` → `fonte.colunas`. Depois rode de novo sem marcar a opção.

## Assinar no Outlook

Link do calendário: `https://gist.githubusercontent.com/<seu-usuario>/<GIST_ID>/raw/calendario.ics`
(o log do passo "Publicar no Gist" imprime o link exato).

- **Outlook Web / novo Outlook**: Calendário → *Adicionar calendário* → *Assinar da Web* → cole o link.
- **Outlook clássico (Windows)**: Calendário → *Adicionar Calendário* → *Da Internet...* → cole o link.

Para compartilhar com colegas, basta enviar o mesmo link.

Observações:
- Quem decide quando atualizar a assinatura é o Outlook/Exchange (em geral, algumas horas
  depois da mudança); o script roda 4x ao dia (07:45, 11:45, 15:45 e 19:45).
- Eventos sem horário viram "dia inteiro" e todos ficam como *Livre*, sem bloquear a agenda.
- O gist secreto não aparece em buscas, mas quem tiver o link consegue ver o calendário
  (datas públicas da B3 + a lista da sua cobertura).
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
