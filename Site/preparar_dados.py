"""
preparar_dados.py — Gera a base de consulta do site a partir do pipeline

Entrada : dataset_consolidado_v<X>.csv   (notebook 01)
          modelo_campeao_v<X>.pkl        (notebook 08: pipeline + model_card)
Saída   : data/carteira_site_v<X>.csv

O site não recebe o consolidado inteiro. Ele precisa só do que o modelo usa
para escorar um cliente — as features do model_card — e do ID_PESSOA para a
busca. Todo o resto (componentes do alvo, aging, produto favorito, datas) fica
fora: é o princípio da necessidade (LGPD, art. 6º, III) aplicado ao deploy.

A única coluna além das features é ALTO_RISCO_REGRA, o rótulo que a regra de
negócio atribui ao cliente. Ela existe para o simulador mostrar, lado a lado,
o que o modelo prevê e o que a regra observou.

O tratamento de ausentes replica o dos notebooks 04 e 08 — zero para contagem,
mediana para TICKET_MEDIO. Se divergisse, o site escoraria clientes de um
jeito diferente daquele com que o modelo foi treinado.

A regra fica em `montar_carteira()`, usada por este script (fonte local) e pelo
site quando a fonte é o Databricks — assim os dois caminhos montam a carteira
do mesmo jeito.

Uso:
    python preparar_dados.py
    python preparar_dados.py --consolidado <csv> --modelo <pkl>
"""

import argparse
import os
import pickle
import sys

import compat  # noqa: F401  — antes do pickle.load (ver compat.py)
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(BASE_DIR)

PADRAO_CONSOLIDADO = os.path.join(RAIZ, "Desenvolvimento", "data", "dataset_consolidado_v1_0.csv")
PADRAO_MODELO = os.path.join(BASE_DIR, "modelo", "modelo_campeao_v1_0.pkl")

# Mesma guarda dos notebooks: identificação nominal nunca chega ao site.
PADROES_NOMINAIS = ("NOME", "NM_", "FANTASIA", "RAZAO", "CPF", "CNPJ",
                    "EMAIL", "TELEFONE", "ENDERECO")


COLS_ALVO = ["TAXA_ATRASO_PAGAMENTO", "TAXA_ATRASO_COMODATO"]
COLS_POPULACAO = ["CORE_BUSINESS", "FREQUENCIA_COMPRAS", "DIAS_DESDE_ULTIMA_COMPRA"]


def colunas_necessarias(card: dict) -> list[str]:
    """As únicas colunas do consolidado que o site precisa ler."""
    cols = ["ID_PESSOA"] + card["features_numericas"] + card["features_categoricas"]
    return list(dict.fromkeys(cols + COLS_ALVO + COLS_POPULACAO))


def montar_carteira(dados: pd.DataFrame, card: dict) -> pd.DataFrame:
    """Consolidado (CSV do notebook 01 ou tabela Delta do 02) → base do site."""
    num = card["features_numericas"]
    cat = card["features_categoricas"]
    pop = card["cenario"]["populacao_regra"]
    alvo = card["cenario"]["alvo_regra"]

    dados = dados.copy()
    dados.columns = [str(c).strip().upper() for c in dados.columns]

    nominais = [c for c in num + cat if any(padrao in c for padrao in PADROES_NOMINAIS)]
    if nominais:
        raise ValueError(f"Identificação nominal entre as features do modelo: {nominais}")
    faltando = sorted(set(colunas_necessarias(card)) - set(dados.columns))
    if faltando:
        raise KeyError(f"Colunas ausentes no consolidado: {faltando}")

    dados["ID_PESSOA"] = pd.to_numeric(dados["ID_PESSOA"], errors="coerce")
    for c in set(num + COLS_ALVO + COLS_POPULACAO):
        dados[c] = pd.to_numeric(dados[c], errors="coerce")
    for c in cat:
        dados[c] = dados[c].fillna("NÃO INFORMADO")

    # Mesmo tratamento do notebook 04/08
    zero = [c for c in num if c != "TICKET_MEDIO"]
    dados[zero] = dados[zero].fillna(0)
    if "TICKET_MEDIO" in num:
        dados["TICKET_MEDIO"] = dados["TICKET_MEDIO"].fillna(dados["TICKET_MEDIO"].median())

    # Quem pertence à população em que o modelo treinou (regra do model_card)
    na_pop = dados["FREQUENCIA_COMPRAS"] > pop["min_compras"]
    if pop["core"]:
        na_pop &= dados["CORE_BUSINESS"] == 1
    if pop["janela_dias"] is not None:
        na_pop &= dados["DIAS_DESDE_ULTIMA_COMPRA"] <= pop["janela_dias"]

    # Universo escorado: o mesmo da carteira escorada do notebook 08
    universo = dados[dados["CORE_BUSINESS"] == 1] if pop["core"] else dados

    atraso_pag = universo["TAXA_ATRASO_PAGAMENTO"].fillna(0) > alvo["limite"]
    atraso_com = universo["TAXA_ATRASO_COMODATO"].fillna(0) > alvo["limite"]
    regra = (atraso_pag | atraso_com) if alvo["combinador"] == "OU" else (atraso_pag & atraso_com)

    saida = universo[["ID_PESSOA"] + num + cat].copy()
    saida["NO_TREINO"] = na_pop.loc[universo.index].astype(int)
    # Fora da população o rótulo não foi usado no treino: fica vazio de propósito
    saida["ALTO_RISCO_REGRA"] = regra.astype("Int64").where(saida["NO_TREINO"] == 1)
    saida["_DATA_VERSION"] = card["data_version"]
    return saida


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--consolidado", default=PADRAO_CONSOLIDADO)
    p.add_argument("--modelo", default=PADRAO_MODELO)
    p.add_argument("--saida", default=None)
    args = p.parse_args()

    with open(args.modelo, "rb") as f:
        card = pickle.load(f)["model_card"]
    versao = card["data_version"]

    dados = pd.read_csv(args.consolidado, sep=";", encoding="utf-8-sig", low_memory=False)
    try:
        saida = montar_carteira(dados, card)
    except (KeyError, ValueError) as e:
        sys.exit(str(e))

    num, cat = card["features_numericas"], card["features_categoricas"]
    destino = args.saida or os.path.join(
        BASE_DIR, "data", f"carteira_site_v{versao.replace('.', '_')}.csv"
    )
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    saida.to_csv(destino, sep=";", encoding="utf-8-sig", index=False)

    print(f"Consolidado : {os.path.basename(args.consolidado)}  ({len(dados):,} clientes)")
    print(f"Modelo     : {os.path.basename(args.modelo)}  (DATA_VERSION {versao})")
    print(f"Saída       : {destino}")
    print(f"  {len(saida):,} clientes × {saida.shape[1]} colunas "
          f"({len(num)} numéricas + {len(cat)} categóricas + ID + 3 de controle)")
    print(f"  na população de treino: {int(saida['NO_TREINO'].sum()):,}")


if __name__ == "__main__":
    main()
