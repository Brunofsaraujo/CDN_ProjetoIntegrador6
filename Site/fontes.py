"""
fontes.py — De onde o site lê modelo, carteira e gráficos

Dois caminhos com a mesma interface, escolhidos em /admin/fonte:

  local       modelo/modelo_campeao_v1_0.pkl
              data/carteira_site_v1_0.csv         (preparar_dados.py)
              restrito/graficos/*.png

  databricks  <VOLUME>/modelo_campeao_v1_0.pkl    Files API (Unity Catalog Volume)
              <TABELA> (consolidado Delta)        SQL Statement API → montar_carteira()
              <VOLUME>/graficos/*.png             Files API

A carteira do Databricks passa pela mesma montar_carteira() do preparar_dados.py:
os dois caminhos aplicam a mesma regra, e o SELECT lê só as colunas necessárias.

Configuração do Databricks: variáveis de ambiente ou instance/databricks.json
(o ambiente tem precedência). O token NUNCA é digitado no site nem gravado em log.
"""

import io
import json
import os
import pickle
import re
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime

import compat  # noqa: F401  — antes de qualquer pickle.load (ver compat.py)
import pandas as pd
import requests
import sklearn

from preparar_dados import colunas_necessarias, montar_carteira

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
ARQ_FONTE = os.path.join(INSTANCE_DIR, "fonte.json")
ARQ_DATABRICKS = os.path.join(INSTANCE_DIR, "databricks.json")

FONTES = {"local": "Local", "databricks": "Databricks"}

PADRAO_DATABRICKS = {
    "host": "",
    "token": "",
    "warehouse_id": "",
    "tabela": "",
    "volume": "",
    "arquivo_modelo": "",
    "pasta_graficos": "",
    "timeout": 60,
}
ENV_DATABRICKS = {
    "host": "DATABRICKS_HOST",
    "token": "DATABRICKS_TOKEN",
    "warehouse_id": "DATABRICKS_WAREHOUSE_ID",
    "tabela": "DATABRICKS_TABELA",
    "volume": "DATABRICKS_VOLUME",
    "arquivo_modelo": "DATABRICKS_ARQUIVO_MODELO",
    "pasta_graficos": "DATABRICKS_PASTA_GRAFICOS",
}

_IDENT = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){2}$")  # catalogo.schema.tabela
_COLUNA = re.compile(r"^[A-Za-z0-9_]+$")


class FonteIndisponivel(Exception):
    """Falha ao carregar de uma fonte — mensagem pronta para o administrador."""


@dataclass
class Carga:
    """Tudo o que o site precisa, carregado de uma fonte."""

    fonte: str
    pipeline: object
    card: dict
    carteira: pd.DataFrame
    origem: dict = field(default_factory=dict)  # de onde veio cada artefato
    avisos: list = field(default_factory=list)
    carregado_em: str = field(
        default_factory=lambda: datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    )
    segundos: float = 0.0


# ──────────────────────────────────────────────────────────────────────────────
#  ESCOLHA PERSISTIDA
# ──────────────────────────────────────────────────────────────────────────────
def fonte_escolhida() -> str:
    """instance/fonte.json > FONTE_DADOS > 'local'."""
    try:
        with open(ARQ_FONTE, encoding="utf-8") as f:
            valor = json.load(f).get("fonte")
        if valor in FONTES:
            return valor
    except (OSError, ValueError):
        pass
    valor = os.getenv("FONTE_DADOS", "local").lower()
    return valor if valor in FONTES else "local"


def gravar_fonte(fonte: str, usuario: str) -> None:
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    with open(ARQ_FONTE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "fonte": fonte,
                "alterado_por": usuario,
                "alterado_em": datetime.now().isoformat(timespec="seconds"),
            },
            f,
            indent=2,
        )


def config_databricks() -> dict:
    cfg = dict(PADRAO_DATABRICKS)
    try:
        with open(ARQ_DATABRICKS, encoding="utf-8") as f:
            cfg.update(
                {
                    k: v
                    for k, v in json.load(f).items()
                    if k in cfg and v not in ("", None)
                }
            )
    except (OSError, ValueError):
        pass
    for chave, env in ENV_DATABRICKS.items():
        if os.getenv(env):
            cfg[chave] = os.getenv(env)
    cfg["host"] = str(cfg["host"]).rstrip("/")
    if cfg["host"] and not cfg["host"].startswith("http"):
        cfg["host"] = "https://" + cfg["host"]
    cfg["volume"] = "/" + str(cfg["volume"]).strip("/")
    return cfg


def resumo_config_databricks() -> dict:
    """Versão segura para exibir na tela: o token aparece só como presente/ausente."""
    cfg = config_databricks()
    faltando = [k for k in ("host", "token", "warehouse_id") if not cfg[k]]
    return {
        "host": cfg["host"] or "—",
        "token": "configurado" if cfg["token"] else "ausente",
        "warehouse_id": cfg["warehouse_id"] or "—",
        "tabela": cfg["tabela"],
        "modelo": f"{cfg['volume']}/{cfg['arquivo_modelo']}",
        "graficos": f"{cfg['volume']}/{cfg['pasta_graficos']}/",
        "faltando": faltando,
        "origem_config": (
            "instance/databricks.json + variáveis de ambiente"
            if os.path.exists(ARQ_DATABRICKS)
            else "variáveis de ambiente"
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
#  PACOTE (.pkl)
# ──────────────────────────────────────────────────────────────────────────────
def _abrir_pacote(dados: bytes, origem: str) -> tuple[object, dict, list]:
    avisos = []
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always")
        try:
            pacote = pickle.load(io.BytesIO(dados))
        except Exception as e:
            raise FonteIndisponivel(
                f"Não foi possível abrir o modelo de {origem}: {e}"
            ) from e
    for w in capturados:
        if w.category.__name__ == "InconsistentVersionWarning":
            avisos.append(
                f"O modelo foi gerado com scikit-learn {getattr(w.message, 'original_sklearn_version', '?')} "
                f"e o site usa {sklearn.__version__}. Gere o .pkl com a mesma versão ou ajuste o requirements.txt."
            )
            break
    if (
        not isinstance(pacote, dict)
        or "pipeline" not in pacote
        or "model_card" not in pacote
    ):
        raise FonteIndisponivel(
            f"{origem} não é um pacote do notebook 08 (esperado: pipeline + model_card)."
        )
    versao_pacote = pacote["model_card"].get("sklearn_version")
    if versao_pacote and versao_pacote != sklearn.__version__ and not avisos:
        avisos.append(
            f"O model card registra scikit-learn {versao_pacote} e o site usa "
            f"{sklearn.__version__}. Alinhe o requirements.txt à versão do pacote."
        )
    return pacote["pipeline"], pacote["model_card"], avisos


# ──────────────────────────────────────────────────────────────────────────────
#  FONTE LOCAL
# ──────────────────────────────────────────────────────────────────────────────
class FonteLocal:
    nome = "local"

    def __init__(self):
        self.modelo = os.getenv(
            "MODEL_PATH", os.path.join(BASE_DIR, "modelo", "modelo_campeao_v1_0.pkl")
        )
        self.carteira = os.getenv(
            "DATASET_PATH", os.path.join(BASE_DIR, "data", "carteira_site_v1_0.csv")
        )
        self.graficos = os.path.join(BASE_DIR, "restrito", "graficos")

    def descricao(self) -> dict:
        return {
            "modelo": self.modelo,
            "carteira": self.carteira,
            "graficos": self.graficos + os.sep,
        }

    def carregar(self) -> Carga:
        inicio = time.time()
        if not os.path.exists(self.modelo):
            raise FonteIndisponivel(
                f"Modelo não encontrado em {self.modelo}. Copie o modelo_campeao do notebook 08 para Site/modelo/."
            )
        if not os.path.exists(self.carteira):
            raise FonteIndisponivel(
                f"Carteira não encontrada em {self.carteira}. Rode: python preparar_dados.py"
            )
        with open(self.modelo, "rb") as f:
            pipeline, card, avisos = _abrir_pacote(f.read(), "arquivo local")
        carteira = pd.read_csv(
            self.carteira, sep=";", encoding="utf-8-sig", low_memory=False
        )
        return Carga(
            "local",
            pipeline,
            card,
            carteira,
            self.descricao(),
            avisos,
            segundos=time.time() - inicio,
        )

    def ler_grafico(self, nome: str) -> bytes:
        caminho = os.path.join(self.graficos, nome)
        if not os.path.isfile(caminho):
            raise FileNotFoundError(nome)
        with open(caminho, "rb") as f:
            return f.read()


# ──────────────────────────────────────────────────────────────────────────────
#  FONTE DATABRICKS
# ──────────────────────────────────────────────────────────────────────────────
class FonteDatabricks:
    nome = "databricks"

    def __init__(self, cfg: dict | None = None, sessao: requests.Session | None = None):
        self.cfg = cfg or config_databricks()
        self.http = sessao or requests.Session()
        self.http.headers["Authorization"] = f"Bearer {self.cfg['token']}"
        self._cache_graficos: dict[str, bytes] = {}

    def descricao(self) -> dict:
        c = self.cfg
        return {
            "modelo": f"{c['volume']}/{c['arquivo_modelo']}",
            "carteira": f"{c['tabela']} (SQL warehouse {c['warehouse_id']})",
            "graficos": f"{c['volume']}/{c['pasta_graficos']}/",
        }

    def _validar_config(self) -> None:
        faltando = [k for k in ("host", "token", "warehouse_id") if not self.cfg[k]]
        if faltando:
            nomes = ", ".join(ENV_DATABRICKS[k] for k in faltando)
            raise FonteIndisponivel(
                f"Configuração do Databricks incompleta: defina {nomes}."
            )
        if not _IDENT.match(self.cfg["tabela"]):
            raise FonteIndisponivel(
                f"Nome de tabela inválido: {self.cfg['tabela']} (use catalogo.schema.tabela)."
            )

    def _get(self, caminho: str) -> requests.Response:
        try:
            r = self.http.get(self.cfg["host"] + caminho, timeout=self.cfg["timeout"])
        except requests.RequestException as e:
            raise FonteIndisponivel(f"Sem conexão com {self.cfg['host']}: {e}") from e
        return r

    def _erro_http(self, r: requests.Response, contexto: str) -> FonteIndisponivel:
        if r.status_code in (401, 403):
            return FonteIndisponivel(
                f"{contexto}: acesso negado ({r.status_code}). Confira o token e as permissões."
            )
        if r.status_code == 404:
            return FonteIndisponivel(f"{contexto}: não encontrado (404).")
        return FonteIndisponivel(f"{contexto}: HTTP {r.status_code} — {r.text[:300]}")

    def _ler_arquivo(self, caminho_volume: str) -> bytes:
        r = self._get("/api/2.0/fs/files" + caminho_volume)
        if r.status_code != 200:
            raise self._erro_http(r, f"Arquivo {caminho_volume}")
        return r.content

    def _consultar(self, sql: str) -> pd.DataFrame:
        """SQL Statement Execution API, resultado INLINE em JSON."""
        url = self.cfg["host"] + "/api/2.0/sql/statements"
        corpo = {
            "warehouse_id": self.cfg["warehouse_id"],
            "statement": sql,
            "wait_timeout": "30s",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
            "on_wait_timeout": "CONTINUE",
        }
        try:
            r = self.http.post(url, json=corpo, timeout=self.cfg["timeout"])
        except requests.RequestException as e:
            raise FonteIndisponivel(f"Sem conexão com {self.cfg['host']}: {e}") from e
        if r.status_code != 200:
            raise self._erro_http(r, "Consulta SQL")
        resp = r.json()

        limite = time.time() + float(self.cfg["timeout"]) * 3
        while resp.get("status", {}).get("state") in ("PENDING", "RUNNING"):
            if time.time() > limite:
                raise FonteIndisponivel(
                    "Consulta SQL: o warehouse não respondeu a tempo (está ligado?)."
                )
            time.sleep(2)
            r = self._get(f"/api/2.0/sql/statements/{resp['statement_id']}")
            if r.status_code != 200:
                raise self._erro_http(r, "Consulta SQL")
            resp = r.json()

        estado = resp.get("status", {}).get("state")
        if estado != "SUCCEEDED":
            msg = resp.get("status", {}).get("error", {}).get("message", estado)
            raise FonteIndisponivel(f"Consulta SQL falhou: {msg}")

        colunas = [c["name"] for c in resp["manifest"]["schema"]["columns"]]
        resultado = resp.get("result", {}) or {}
        linhas = list(resultado.get("data_array", []) or [])
        proximo = resultado.get("next_chunk_internal_link")
        while proximo:  # resultados grandes vêm em blocos
            r = self._get(proximo)
            if r.status_code != 200:
                raise self._erro_http(r, "Consulta SQL (bloco)")
            bloco = r.json()
            linhas.extend(bloco.get("data_array", []) or [])
            proximo = bloco.get("next_chunk_internal_link")
        return pd.DataFrame(linhas, columns=colunas)

    def carregar(self) -> Carga:
        inicio = time.time()
        self._validar_config()
        caminho_modelo = f"{self.cfg['volume']}/{self.cfg['arquivo_modelo']}"
        pipeline, card, avisos = _abrir_pacote(
            self._ler_arquivo(caminho_modelo), caminho_modelo
        )

        colunas = colunas_necessarias(card)
        invalidas = [c for c in colunas if not _COLUNA.match(c)]
        if invalidas:
            raise FonteIndisponivel(
                f"Nome de coluna inesperado no model_card: {invalidas}"
            )
        sql = f"SELECT {', '.join('`' + c + '`' for c in colunas)} FROM {self.cfg['tabela']}"
        try:
            carteira = montar_carteira(self._consultar(sql), card)
        except (KeyError, ValueError) as e:
            raise FonteIndisponivel(f"Tabela {self.cfg['tabela']}: {e}") from e

        return Carga(
            "databricks",
            pipeline,
            card,
            carteira,
            self.descricao(),
            avisos,
            segundos=time.time() - inicio,
        )

    def ler_grafico(self, nome: str) -> bytes:
        if nome not in self._cache_graficos:
            try:
                self._cache_graficos[nome] = self._ler_arquivo(
                    f"{self.cfg['volume']}/{self.cfg['pasta_graficos']}/{nome}"
                )
            except FonteIndisponivel as e:
                raise FileNotFoundError(str(e)) from e
        return self._cache_graficos[nome]


def criar_fonte(nome: str):
    return FonteDatabricks() if nome == "databricks" else FonteLocal()
