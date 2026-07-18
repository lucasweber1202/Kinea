# Coletor ECB - preditores da inflação tcheca

Entrega completa do desafio técnico da Kinea: um coletor específico para o **Banco Central
Europeu (ECB)** que captura os quatro componentes do HICP tcheco e o câmbio EUR/CZK,
armazena os dados no esquema relacional obrigatório, preserva revisões (vintages), é
idempotente e registra exatamente um log por execução. A apresentação é um dashboard
Streamlit com consulta histórica `as-of`.

O coletor (`kinea/`) usa apenas a biblioteca padrão do Python. O dashboard é a única parte
com dependências externas.

## Revisão rápida, sem executar nada

| O que avaliar | Arquivo |
|---|---|
| DDL exato de `metadata`, `time_series` e `logs` | [`kinea/db.py`](kinea/db.py) |
| Quatro regras de vintages e idempotência | [`kinea/vintages.py`](kinea/vintages.py) |
| Coleta HTTP com timeout, retry e backoff | [`kinea/client.py`](kinea/client.py) |
| Log em `finally`, inclusive em erro | [`kinea/collector.py`](kinea/collector.py) |
| Banco SQLite preenchido com dados reais | [`evidence/kinea.db`](evidence/kinea.db) |
| Prova da segunda execução sem novas linhas | [`evidence/idempotency.txt`](evidence/idempotency.txt) |
| Duas versões coexistindo e consulta as-of | [`evidence/revision_demo.db`](evidence/revision_demo.db), [`evidence/revision_demo.txt`](evidence/revision_demo.txt) |
| Consulta de amostra e saída | [`evidence/sample_query.sql`](evidence/sample_query.sql), [`evidence/sample_query_output.txt`](evidence/sample_query_output.txt) |
| Logs de sucesso e erro | [`evidence/log_success.txt`](evidence/log_success.txt), [`evidence/log_error.txt`](evidence/log_error.txt) |
| 23 testes automatizados | [`tests/`](tests/) |
| Dashboard | [`dashboard/app.py`](dashboard/app.py) e capturas em [`docs/`](docs/) |

## Fonte e séries

Escolhi o ECB porque a API SDMX é pública, dispensa chave e publica exatamente as cinco
séries pedidas no catálogo do desafio. O catálogo separa nosso identificador estruturado do
código externo do ECB:

| `series_id` interno | Código ECB | Frequência | Unidade | Papel preditivo |
|---|---|---|---|---|
| `CZ_FX_EURCZK` | `EXR.D.CZK.EUR.SP00.A` | daily | currency | repasse cambial |
| `CZ_HICP_CORE_INDEX` | `HICP.M.CZ.N.XEF000.4D0.INX` | monthly | index | inflação subjacente |
| `CZ_HICP_ENERGY_INDEX` | `HICP.M.CZ.N.NRGY00.4D0.INX` | monthly | index | choque de energia |
| `CZ_HICP_FOOD_INDEX` | `HICP.M.CZ.N.FOOD00.4D0.INX` | monthly | index | preços de alimentos |
| `CZ_HICP_SERVICES_INDEX` | `HICP.M.CZ.N.SERV00.4D0.INX` | monthly | index | inflação de serviços |

Os quatro componentes são guardados em nível de índice (**2025 = 100**), a forma mais crua
disponível, e não como variação anual. Em 2026, o ECB passou a exibir essas séries no fluxo
vigente `HICP`, com provedor/contexto `4D0`; o coletor não usa o fluxo antigo `ICP`. Os códigos
acima foram confrontados com o [portal oficial de HICP do ECB](https://data.ecb.europa.eu/data/concepts/hicp).

O banco principal versionado foi coletado ao vivo em 18/07/2026: **8.327 observações reais**
(7.051 diárias de câmbio e 319 mensais em cada componente HICP). A repetição imediata inseriu
zero linhas; veja [`evidence/live_validation.txt`](evidence/live_validation.txt).

`parse_series_id()` valida e decompõe os IDs internos. `name` e `description` são gerados a
partir desses tokens - nenhum nome é escrito manualmente por série.

## Instalação

Requer Python 3.11 ou superior.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev,dashboard]"
```

A fonte não exige chave, token, `.env` ou qualquer segredo.

## Executar de ponta a ponta

### Coleta real

```bash
python -m kinea.cli collect --mode live --db data/kinea.db
python -m kinea.cli status --db data/kinea.db
```

Depois da primeira carga completa, uma janela recente pode reduzir o trabalho e ainda capturar
revisões tardias:

```bash
python -m kinea.cli collect --mode live --months 12 --db data/kinea.db
```

Falhas graves de rede são propagadas, mas antes de o processo terminar uma linha `error`, com
traceback, é gravada em `logs`. Registros isolados inválidos geram warning e os demais continuam.

### Reprodução totalmente offline

```bash
python -m kinea.cli collect --mode offline --fixtures fixtures/v1 --db /tmp/kinea.db
python -m kinea.cli collect --mode offline --fixtures fixtures/v2 --db /tmp/kinea.db
python -m kinea.cli collect --mode offline --fixtures fixtures/v2 --db /tmp/kinea.db
```

`fixtures/v1` e `fixtures/v2` são dados **sintéticos e determinísticos**, no formato SDMX-CSV do
ECB. `v2` adiciona observações e revisa duas já existentes; a terceira execução é idempotente.
Eles existem apenas como contingência reprodutível quando o host da API não está acessível. O
caminho de parser e banco é o mesmo do modo live; o banco principal desta entrega, porém, contém
dados reais.

### Refazer todas as evidências

```bash
python scripts/generate_evidence.py              # live; fallback offline automático
python scripts/generate_evidence.py --mode live  # exige ECB acessível; sem fallback
python scripts/generate_evidence.py --mode offline
python scripts/demo_revision.py
python -m pytest -q
```

O primeiro comando recria os nove artefatos principais e o banco separado
`revision_demo.db`. A separação é intencional: a revisão simulada fica visível e executável sem
alterar qualquer número oficial do banco real.

## Dashboard (Parte B)

```bash
streamlit run dashboard/app.py
# banco alternativo:
streamlit run dashboard/app.py -- --db data/kinea.db
```

As cinco abas explicam cobertura, componentes do HICP, EUR/CZK, revisões e logs. Em
**Vintages & as-of**, o usuário escolhe uma data e vê somente o que já era conhecido naquele
dia. Quando o banco real ainda não contém uma revisão observada, essa aba usa automaticamente a
demonstração rotulada de `revision_demo.db`.

![Visão geral](docs/dashboard-overview.png)

![Consulta as-of](docs/dashboard-as-of.png)

## Modelo de dados e regras

O banco contém somente as três tabelas obrigatórias:

- `metadata`: uma linha por `series_id`, com cobertura calculada de `time_series`;
- `time_series`: chave `(series_id, reference_date, vintage_date)`;
- `logs`: uma linha por execução, inserida em `finally`.

As regras de `time_series` são implementadas diretamente e cobertas por testes:

1. primeira observação: insere com `vintage_date` igual ao dia da coleta;
2. mesmo valor em nova coleta: não altera nem `collected_at`;
3. valor diferente em dia posterior: acrescenta nova linha e preserva a anterior;
4. valor diferente no mesmo dia: atualiza a linha desse dia (a última coleta vence).

A visão atual e a fotografia histórica usam `ROW_NUMBER()`; não há `is_current`, `run_id` ou
outro estado paralelo. Exemplo:

```bash
python -m kinea.cli as-of --db evidence/revision_demo.db --date 2026-07-10 \
  --series CZ_HICP_CORE_INDEX
python -m kinea.cli vintages --db evidence/revision_demo.db \
  --series CZ_HICP_CORE_INDEX --reference-date 2026-06-01
```

## Estrutura

```text
config/series.json          catálogo interno -> códigos ECB
kinea/db.py                 DDL e consultas current/as-of
kinea/identifiers.py        parser e nomes derivados do series_id
kinea/client.py             HTTP com retry/backoff + fixtures offline
kinea/parser.py             SDMX-CSV robusto
kinea/vintages.py           regras de revisão e idempotência
kinea/collector.py          transação e log garantido em finally
kinea/cli.py                collect/status/as-of/vintages
dashboard/app.py            apresentação Streamlit
fixtures/v1, fixtures/v2    dados sintéticos reprodutíveis
scripts/                    gerador de evidências e demo de revisão
evidence/                   banco + provas prontas para inspeção
tests/                      23 testes
```

## Decisões de engenharia

- **SQLite e SQL explícito:** arquivo único auditável, DDL igual ao enunciado e parâmetros em
  todos os valores externos.
- **Transação por execução:** uma falha grave desfaz alterações parciais; o log de erro é gravado
  depois do rollback.
- **Sem hash ou flag de corrente:** comparação direta com o último valor e consulta por janela
  mantêm o modelo alinhado ao contrato pedido.
- **Proteção contra vintages retroativos:** o ingest rejeita uma data de coleta anterior ao último
  vintage conhecido, evitando inventar conhecimento histórico.
