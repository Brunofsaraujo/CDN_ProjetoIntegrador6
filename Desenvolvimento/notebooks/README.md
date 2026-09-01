# 🍺 Chopp & Cia — Pipeline de Inteligência de Risco (v1.0)

Projeto Integrador VI · FATEC Votorantim · 2º Semestre/2026

---

## Os seis notebooks

| # | Notebook | Responsabilidade | Entrada | Saída |
|:-:|:---|:---|:---|:---|
| 01 | `Chopp_Risco_01_Ingestao_v1.0` | CSV consolidado → tabela versionada | `.csv` | `dataset_consolidado_v<VER>` |
| 02 | `Chopp_Risco_02_EDA_v1.0` | entender a carteira e o risco | tabela do 01 | análises (não escreve dado) |
| 03 | `Chopp_Risco_03_Preparacao_v1.0` | alvo + split congelado | tabela do 01 | `dataset_split_<HASH>` |
| 04 | `Chopp_Risco_04_Modelo_Regressao_v1.0` | Regressão Logística | split do 03 | runs MLflow + modelo |
| 05 | `Chopp_Risco_05_Modelo_Arvore_v1.0` | Árvore de Decisão | split do 03 | runs MLflow + modelo |
| 06 | `Chopp_Risco_06_Modelo_RandomForest_v1.0` | Random Forest | split do 03 | runs MLflow + modelo |

Os notebooks são **autossuficientes**: não há `%run` nem import entre eles. O
acoplamento é por **dado** (nome de tabela), não por código.

---

## Fluxo de execução

```
   01 Ingestão                      DATA_VERSION = "1.0"
        │                           ↓ incremente ao trocar o CSV ou o contrato
        │   publica
        ▼
   dataset_consolidado_v1_0  ────────────┐
        │                                │
        │ TABELA_ENTRADA                 │ TABELA_ENTRADA
        ▼                                ▼
   02 EDA                           03 Preparação
   (só leitura)                          │  publica
                                         ▼
                              dataset_split_<SPLIT_HASH>
                                         │
                    ┌────────────────────┼────────────────────┐
                    │ TABELA_SPLIT       │                    │
                    ▼                    ▼                    ▼
              04 Regressão          05 Árvore         06 Random Forest
                    └────────────────────┼────────────────────┘
                                         ▼
                                   MLflow · mesmo split_hash
                                   ⇒ comparação pareada
```

### Primeira execução

Nenhum caminho de arquivo está escrito no código. Cada notebook resolve seus
parâmetros conforme o ambiente:

| Ambiente | Mecanismo |
|:---|:---|
| **Databricks** | `dbutils.widgets` — campos no topo do notebook |
| **Local** (VS Code / Jupyter) | diálogo de seleção do Windows |

**No Databricks:**

1. Faça upload do CSV consolidado em um Volume.
2. **01** — preencha `catalogo`, `schema`, `data_version` e o caminho do CSV no
   Volume. Ao final, ele imprime o nome da tabela publicada.
3. **02** e **03** — preencha `catalogo`, `schema` e `data_version` (a mesma do 01).
4. **03** — ao final, imprime o `SPLIT_HASH`. Cole-o no campo `split_hash` dos
   **04, 05 e 06**.

**Localmente:** a primeira célula de código abre o seletor do Windows. O notebook 03
salva o split ao lado do CSV consolidado.

Os notebooks 04-06 **recusam-se a rodar** sem o split definido — apontar para a
partição errada invalidaria a comparação entre os modelos.

---

## As três identidades

Todo run do MLflow carrega as três. Uma diferença de métrica sempre tem endereço.

| Hash | Definido em | Cobre | Responde |
|:---|:---|:---|:---|
| `DATA_VERSION` | 01 (manual) | o CSV consolidado publicado | *quais dados?* |
| `SPLIT_HASH` | 03 (automático) | universo + alvo + partição | *quais linhas, com qual rótulo?* |
| `CONFIG_HASH` | 04-06 (automático) | hiperparâmetros | *qual configuração?* |

### Quando incrementar `DATA_VERSION`

Sempre que mudar o contrato de colunas ou substituir o CSV consolidado de origem.
Cada versão vira uma **tabela nova**; as antigas continuam existindo, e é isso que
mantém reproduzíveis os experimentos que apontam para elas.

O notebook 01 **bloqueia** a republicação sobre uma versão existente. Para forçar,
`PERMITIR_SOBRESCRITA = True` — sabendo que os runs que referenciam aquela versão
deixarão de ser reproduzíveis.

---

## Os estudos (notebooks 04-06)

Cada notebook executa **um lote por vez**. Troque `ESTUDO` no painel:

| `ESTUDO` | Varre | Responde |
|:---|:---|:---|
| `hiperparametros` | um parâmetro do algoritmo | qual configuração aprende melhor |
| `grade_cruzada` | produto de dois ou mais parâmetros | há interação entre eles |
| `elegibilidade` | `min_compras` | quantos clientes vale a pena usar |
| `janela_temporal` | recência máxima | qual histórico generaliza melhor |
| `ablacao` | remove features derivadas do alvo | quanto do desempenho vem do vazamento |
| `baseline_unico` | nada | rodada de referência |

Os estudos `elegibilidade` e `janela_temporal` **filtram o split já congelado** em vez
de reparticionar — um cliente que está no teste continua no teste em todas as
variações.

---

## Como navegar no MLflow

Estrutura de cada lote:

```
Chopp_Cia_Experimentos
└── LOTE__<estudo>__<modelo>__<split_hash>__<data>     run pai
    ├── baseline (classe majoritária)
    ├── <eixo>=<valor 1>
    └── <eixo>=<valor 2>
```

| Pergunta | Filtro |
|:---|:---|
| qual algoritmo vence? | `tags.split_hash = '<hash>'`, agrupe por `tags.modelo` |
| quantos clientes usar? | `tags.estudo = 'elegibilidade'`, ordene por `metrics.test_mcc` |
| qual janela temporal? | `tags.estudo = 'janela_temporal'` |
| efeito de um parâmetro? | `tags.eixo_varrido = '<parametro>'` |
| quanto vem do vazamento? | `tags.estudo = 'ablacao'` |

> Comparar modelos só é válido com o **mesmo `split_hash`**. Hashes diferentes
> significam linhas ou rótulos diferentes — não é comparação pareada.

---

## Parâmetros: nada de caminho fixo

Nenhum dos seis notebooks tem caminho de arquivo escrito no código. Isso resolve dois
problemas concretos:

- **Portabilidade** — qualquer pessoa do grupo roda os notebooks sem editar nada.
- **Exposição** — a estrutura de diretórios da máquina de quem desenvolveu não vai
  para o repositório.

> **Não é controle de acesso.** Quem executa o notebook lê o mesmo arquivo com ou sem
> seletor. O controle de acesso real, no Databricks, vem das permissões do Unity
> Catalog sobre o Volume e as tabelas — `GRANT` por objeto, não caminho no código.

Parâmetros por notebook:

| Notebook | Parâmetros |
|:---|:---|
| 01 | `csv_consolidado`, `catalogo`, `schema`, `data_version` |
| 02 | `catalogo`, `schema`, `data_version` |
| 03 | `catalogo`, `schema`, `data_version` |
| 04-06 | `split_hash`, `csv_split`, `catalogo`, `schema`, `estudo`, `experiment_name` |

---

## Privacidade e viés: sem identificação nominal

`NM_PESSOA` e `DS_FANTASIA` **não fazem parte do contrato de colunas** do notebook 01.
Nenhum nome de cliente é extraído do ERP em ponto algum do pipeline — a unidade de
análise é `ID_PESSOA` do começo ao fim.

A razão não é apenas de proteção de dado pessoal. Nome não tem poder preditivo sobre
inadimplência, e o que ele acrescentaria seria **viés**: um modelo com acesso a nomes
pode aprender clientes específicos em vez de comportamento de risco, passando a
discriminar por identidade onde deveria discriminar por conduta. Numa aplicação de
crédito, é exatamente o erro que não se pode cometer.

Três camadas garantem isso:

| Camada | Onde | O que faz |
|:---|:---|:---|
| Contrato de colunas | 01 | valida o CSV e rejeita colunas ausentes ou nominais |
| Guarda de identificação | 03 | aborta se uma coluna nominal virar feature |
| Exclusão por padrão | 04-06 | qualquer coluna nominal é excluída ao deduzir as features |

As guardas do 03 e dos 04-06 existem para o caso de uma carga futura reintroduzir uma
coluna nominal: sem elas, a coluna voltaria em silêncio. O padrão cobre `NOME`, `NM_`,
`FANTASIA`, `RAZAO`, `CPF`, `CNPJ`, `EMAIL`, `TELEFONE` e `ENDERECO`.

> Para reidentificar um cliente a partir de `ID_PESSOA`, consulte o ERP — é onde o dado
> pessoal deve viver. O pipeline analítico não precisa dele.

---

## Decisões metodológicas

**Campeão por MCC, não AUC.** A classe positiva é maioria; MCC só sobe quando ambas as
classes são bem classificadas, e o baseline majoritário tem MCC = 0 por construção.

**Seleção por CV, não por holdout.** Com poucos casos da classe rara no teste, comparar
muitas variações pelo `test_*` transforma o holdout em parte do processo de escolha.
`SELECAO['origem'] = 'cv'` é o padrão.

**Limiar escolhido out-of-fold.** A varredura roda sobre predições de folds que não
viram o cliente no treino, não sobre o holdout.

**Baseline sempre presente.** Prever "todos são risco" já entrega accuracy alta e
recall perfeito. Nenhuma métrica significa nada sem essa âncora ao lado.

---

## Limitações conhecidas

Registradas em `limitacoes_metodologicas.json` em todo run.

1. **Sem corte temporal** — features e alvo vêm da mesma janela. As métricas medem a
   capacidade de **reproduzir a regra de negócio**, não de prever o futuro.
2. **Vazamento parcial** — `MEDIA_DIAS_ATRASO_*` compartilha origem aritmética com
   `TAXA_ATRASO_*`, que constrói o alvo. Quantificado por `ESTUDO='ablacao'`.
3. **Classe positiva majoritária** — accuracy e AUC isoladas enganam.
4. **`n` pequeno no teste** — intervalos de confiança largos; leia o IC junto do valor.

---

## Tabelas no Unity Catalog

| Tabela | Produzida por | Conteúdo |
|:---|:---|:---|
| `dataset_consolidado_v<VER>` | 01 | 1 linha por cliente |
| `dataset_consolidado_corrente` | 01 | view → última versão (não use em experimento) |
| `catalogo_versoes_dados` | 01 | índice de todas as cargas |
| `dataset_split_<HASH>` | 03 | partição congelada |
| `dataset_producao_<HASH>` | 03 | base de lookup para escoragem |
| `catalogo_splits` | 03 | índice de todos os splits |

Todas gravadas com `saveAsTable()` — **tabelas do catálogo**, consultáveis por SQL e
com comentário de coluna, não arquivos `.parquet` soltos num Volume.

---

## Material anterior

Os notebooks originais do projeto (`Chopp_Risco_EDA_ML_*.ipynb` e
`Extracao_Dados_Consolidados.ipynb`) estão em `Desenvolvimento/backups/`. Toda a
lógica de negócio deles foi preservada nesta arquitetura.
