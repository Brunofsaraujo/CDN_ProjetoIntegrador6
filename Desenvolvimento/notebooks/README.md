# Chopp & Cia — Pipeline de Inteligência de Risco

Projeto Integrador VI · FATEC Votorantim · 2º Semestre/2026

## Os notebooks

| # | Notebook | Responsabilidade | Ambiente |
|:-:|:---|:---|:---|
| 01 | `Chopp_Risco_01_Extracao` | dump `.sql` do ERP → CSV consolidado | Windows local |
| 02 | `Chopp_Risco_02_Ingestao` | CSV consolidado → tabela Delta | Databricks |
| 03 | `Chopp_Risco_03_EDA` | entender a carteira e o risco | local ou Databricks |
| 04 | `Chopp_Risco_04_Preparacao` | alvo + split | local ou Databricks |
| 05 | `Chopp_Risco_05_Modelo_Regressao` | Regressão Logística | local ou Databricks |
| 06 | `Chopp_Risco_06_Modelo_Arvore` | Árvore de Decisão | local ou Databricks |
| 07 | `Chopp_Risco_07_Modelo_RandomForest` | Random Forest | local ou Databricks |
| 08 | `Chopp_Risco_08_Cenarios` | compara cenários: população × alvo × features × modelo | local ou Databricks |

Cada notebook é **autossuficiente**: não há `%run` nem import entre eles. O
acoplamento é por dado — um lê o arquivo ou a tabela que o anterior escreveu.

## Fluxo

```
01 Extracao  (Windows local)
    dump .sql do ERP
         │
         ▼
    dataset_consolidado_v<DATA_VERSION>.csv
         │
         ├──────────────► 02 Ingestao (Databricks) ──► tabela Delta
         │                                                  │
         ▼                                                  ▼
    03 EDA          04 Preparacao ──► dataset_split_v<VER>.csv
                                             │
                          ┌──────────────────┼──────────────────┐
                          ▼                  ▼                  ▼
                    05 Regressao       06 Arvore        07 Random Forest

    08 Cenarios lê o CONSOLIDADO (não o split): a população é um dos
    eixos que ele varre, e o split já tem uma população escolhida dentro.
```

## Duas perguntas diferentes: 05-07 e 08

Os notebooks 05, 06 e 07 respondem *"qual hiperparâmetro é melhor para este
modelo, nesta população"*. O 08 responde a pergunta de fora: *"quais decisões —
de população, de alvo, de features e de modelo — produzem o melhor resultado"*.

A ordem de uso é: **08 primeiro** para escolher o cenário, depois 04 para fixá-lo
e 05-07 para o ajuste fino dentro dele. O 08 não elege modelo de produção — ele
usa configuração fixa por modelo justamente para que a comparação seja entre
cenários, não entre ajustes.

## Local ou Databricks

A separação é explícita em cada notebook:

- **01** roda só em Windows local — parseia o dump do ERP. Não usa Spark nem MLflow.
- **02** roda só no Databricks — publica a tabela no Unity Catalog.
- **03 a 07** rodam nos dois. Localmente leem o CSV e mantêm tudo em memória;
  no Databricks leem a tabela. O processamento é pandas/sklearn nos dois casos.

Nenhum notebook importa `pyspark` ou `mlflow` no topo: as duas bibliotecas só
aparecem dentro do ramo `if EM_DATABRICKS:`. É o que permite executar localmente
sem erro, sem ter Spark instalado.

## Parâmetros

Cada notebook começa por um **painel de controle**: uma única célula com todas
as constantes, hiperparâmetros de arquitetura e de treinamento. As células
seguintes só leem daquele bloco — não há número mágico espalhado pelo notebook.

No notebook 01, preencha `CAMINHO_SQL` com o caminho do dump. Se `PASTA_SAIDA`
ficar vazia, o CSV é gravado ao lado do `.sql`.

## Versão do dado

`DATA_VERSION` nomeia todos os arquivos e tabelas, e é o que mantém reproduzível
um resultado de ontem: cada versão vira arquivo próprio, nada é sobrescrito.

**A versão corrente é a 1.1.** Ela corrige três filtros do ERP que a 1.0 não
aplicava — registro inativo (`FL_ATIVO`), tipo de operação (o dump traz
movimentação de comodato na mesma tabela dos itens vendidos) e pessoa que não é
cliente. O efeito é grande nas features de RFM: `FREQUENCIA_COMPRAS` caiu ~50% e
`TICKET_MEDIO` dobrou, porque o denominador deixou de contar comodato como compra.

Um efeito colateral: `MIN_COMPRAS` no notebook 04 passou de 2 para 0. O corte
antigo fora calibrado sobre a frequência inflada; com a contagem correta ele
deixava 332 clientes e 6 casos na classe rara — validação cruzada inviável.

## O que ficou de fora

O notebook `03b_Validacao_Parametros` foi **absorvido pelo 03** (seções 10 a 14) e
está em `backups/`. Os números fixos no texto dele vieram da v1.0 e não valem mais;
as seções que entraram no EDA recalculam tudo a partir do dado carregado.

## Versionamento

Duas constantes, só:

- **`DATA_VERSION`** — incremente ao trocar o dump ou qualquer regra de negócio
  do notebook 01. Cada versão gera um arquivo próprio
  (`dataset_consolidado_v1_0.csv`), então os resultados antigos continuam
  reproduzíveis.
- **`RANDOM_STATE`** — semente única, propagada a split, CV e estimadores.

O notebook 01 recusa-se a sobrescrever um CSV da mesma versão; use
`SOBRESCREVER = True` conscientemente.

## Sem identificação nominal

`NM_PESSOA` e `DS_FANTASIA` não fazem parte do contrato de colunas. Nenhum nome
de cliente sai do ERP: a unidade de análise é `ID_PESSOA` do começo ao fim.

A razão não é só privacidade. Nome não prevê inadimplência, e o que acrescentaria
seria viés — um modelo com acesso a nomes aprende clientes específicos em vez de
comportamento de risco. Numa aplicação de crédito é o erro que não se pode
cometer. Para reidentificar um cliente, consulte o ERP.

O notebook 01 aborta se uma coluna nominal aparecer no consolidado, e os
notebooks de modelagem excluem por padrão qualquer coluna que case com `NOME`,
`NM_`, `FANTASIA`, `RAZAO`, `CPF`, `CNPJ`, `EMAIL`, `TELEFONE` ou `ENDERECO`.

## Limitações conhecidas

1. **Sem corte temporal** — features e alvo vêm da mesma janela. As métricas
   medem a capacidade de reproduzir a regra de negócio, não de prever o futuro.
2. **Vazamento parcial** — `MEDIA_DIAS_ATRASO_*` compartilha origem aritmética
   com `TAXA_ATRASO_*`, que constrói o alvo.
3. **Classe positiva majoritária** — accuracy e AUC isoladas enganam; o campeão
   é escolhido por MCC.
4. **`n` pequeno no teste** — intervalos de confiança largos.

## Material anterior

Os notebooks originais estão em `Desenvolvimento/backups/`.
