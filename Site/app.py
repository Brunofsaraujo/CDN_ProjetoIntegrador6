"""
app.py — Chopp & Cia | Inteligência de Risco em Comodato
PI6 - FATEC Votorantim | 2026

Endpoints:
  GET  /                    → apresentação do projeto (pública)
  GET  /login, /logout      → autenticação (auth.py)
  GET  /auditoria           → trilha de acesso (admin)
  GET  /admin/fonte         → escolha da fonte: Local ou Databricks (admin)
  POST /api/buscar_cliente  → lookup por ID_PESSOA             [login]
  POST /api/predict         → predição por ID ou dados manuais [login]
  GET  /graficos/<arquivo>  → gráficos do EDA                  [login]
  GET  /health              → estado do serviço

Fontes (fontes.py) — mesma interface, escolhidas pelo administrador:
  local       modelo/*.pkl + data/carteira_site_*.csv + restrito/graficos/
  databricks  Volume (pkl e gráficos) + tabela Delta via SQL warehouse

O que fica público e o que exige login:
  público  → método, pipeline e qualidade do modelo (MCC, AUC, importâncias)
  login    → tudo que é dado da Chopp & Cia: tamanho da carteira, faturamento,
             percentuais de risco, contagens, gráficos do EDA e o perfil e o
             score de cada ID_PESSOA.
Os números da empresa nem chegam ao template sem login (variável `painel`), e os
gráficos ficam fora de static/, servidos só por rota autenticada.
Defina LOGIN_SITE_INTEIRO=1 para exigir login também na apresentação.
"""

import os
import secrets
from datetime import timedelta
from typing import Any

import compat  # noqa: F401  — antes do scikit-learn (ver compat.py)
import pandas as pd
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

import fontes
from auth import (
    INSTANCE_DIR,
    admin_required,
    auth_bp,
    csrf_token,
    csrf_valido,
    init_db,
    login_manager,
    nao_autorizado,
    registrar,
)

LOGIN_SITE_INTEIRO = os.getenv("LOGIN_SITE_INTEIRO", "0") == "1"

# ── Dados da empresa: só vão ao template com usuário autenticado ─────────────
# Saídas do notebook 03 (EDA) e 08 (holdout) sobre o dump de ago/2026,
# DATA_VERSION 1.0. Ao reprocessar, atualizar junto com o modelo.
PAINEL_EMPRESA = {
    "kpis": [
        ("1.429", "clientes na carteira", "bi-people-fill"),
        ("86%", "atrasam mais de 20% dos compromissos", "bi-exclamation-triangle-fill"),
        ("R$ 3,2 mi", "faturamento desses clientes (97% do total)", "bi-cash-stack"),
        ("696", "em risco duplo: devem e estão com chopeira", "bi-box-seam-fill"),
    ],
    "funil": [
        ("Consolidado do ERP", 1429, ""),
        ("Com ao menos 1 compra", 1281, "população de modelagem"),
        ("Core business (chopp/chopeira)", 1279, ""),
        ("Core + 2 ou mais compras", 584, "classe rara: 23 casos"),
        ("Core + 3 ou mais compras", 332, "classe rara: 6 casos — inviável"),
    ],
    "correcao": [
        ("Frequência de compras", "−51%"),
        ("Ticket médio", "+104%"),
        ("Itens de comodato tirados da contagem de vendas", "16.813"),
        ("Clientes que mudaram de classe de risco", "0"),
    ],
    "matriz": {"tn": 134, "fp": 44, "fn": 59, "tp": 148},
    "n_teste": 385,
    "graficos": [
        (
            "composicao_carteira.png",
            "Composição da carteira",
            "1.231 clientes (86%) têm financeiro e comodato ao mesmo tempo.",
        ),
        (
            "carteira_em_risco.png",
            "Quanto da carteira está em risco",
            "1.222 clientes atrasam mais de 20% dos compromissos; respondem por 97% do faturamento.",
        ),
        (
            "aging_atrasos.png",
            "Há quanto tempo os atrasos se arrastam",
            "158 clientes já atrasaram parcela por mais de 30 dias; 83 ficaram com a chopeira além disso.",
        ),
        (
            "risco_por_grupo.png",
            "Cidade, pagamento e tipo de negócio",
            "Grupos com 30+ clientes acima da média de 61% em risco financeiro. Indícios, sem teste estatístico.",
        ),
        (
            "recencia_risco.png",
            "Cliente que sumiu é cliente que não pagou?",
            "Quem comprou no último mês: 84% em risco; quem não compra há um ano: 55%.",
        ),
        (
            "mix_pagamento.png",
            "Quem alterna forma de pagamento",
            "Quem mistura à vista e a prazo tem mais risco (97%) que quem compra sempre a prazo (88%).",
        ),
        (
            "concentracao_faturamento.png",
            "Onde está o dinheiro",
            "Os 25% maiores clientes concentram 80% do faturamento; 89% deles estão em risco.",
        ),
        (
            "comodato_valor.png",
            "O equipamento cedido",
            "A faixa de maior valor concentra 89% do equipamento exposto e o maior risco de não devolução.",
        ),
        (
            "funil_modelagem.png",
            "Do ERP ao modelo",
            "1.429 clientes → 1.281 com compra. Exigir 3+ compras reduziria a base a 332.",
        ),
        (
            "frequencia_compras.png",
            "Compras por cliente",
            "54% dos clientes têm uma única compra; por isso o corte por histórico ficou inviável.",
        ),
        (
            "regua_atraso.png",
            "Onde colocar a régua do atraso",
            "Com “OU”, cerca de 90% da carteira é risco em qualquer régua; com “E”, as classes se equilibram.",
        ),
    ],
}
GRAFICOS_PERMITIDOS = {arq for arq, _, _ in PAINEL_EMPRESA["graficos"]}

# Faixas de leitura do score — as mesmas da carteira escorada do notebook 08
FAIXA_BAIXO, FAIXA_ALTO = 0.35, 0.65

# Rótulos e formato de cada feature numérica no simulador. A LISTA de features
# não vem daqui: vem do model_card, e o que não estiver nele é ignorado.
FEATURE_SPECS = {
    "FREQUENCIA_COMPRAS": {
        "rotulo": "Frequência de compras",
        "ex": "4",
        "step": "1",
        "tipo": "int",
    },
    "TICKET_MEDIO": {
        "rotulo": "Ticket médio (R$)",
        "ex": "450",
        "step": "0.01",
        "tipo": "brl",
    },
    "TOTAL_GASTO": {
        "rotulo": "Total gasto (R$)",
        "ex": "1800",
        "step": "0.01",
        "tipo": "brl",
    },
    "DIAS_DESDE_PRIMEIRA_COMPRA": {
        "rotulo": "Dias desde a 1ª compra",
        "ex": "600",
        "step": "1",
        "tipo": "dias",
    },
    "DIAS_DESDE_ULTIMA_COMPRA": {
        "rotulo": "Dias desde a última compra",
        "ex": "30",
        "step": "1",
        "tipo": "dias",
    },
    "TOTAL_PARCELAS": {
        "rotulo": "Total de parcelas",
        "ex": "4",
        "step": "1",
        "tipo": "int",
    },
    "TOTAL_COMODATOS": {
        "rotulo": "Total de comodatos",
        "ex": "3",
        "step": "1",
        "tipo": "int",
    },
    "PCT_COMPRAS_A_PRAZO": {
        "rotulo": "Compras a prazo (%)",
        "ex": "50",
        "step": "1",
        "tipo": "pct",
        "max": "100",
    },
    "PRAZO_MEDIO_COMODATO": {
        "rotulo": "Prazo médio do comodato (dias)",
        "ex": "3",
        "step": "1",
        "tipo": "dias",
    },
    "VALOR_MEDIO_COMODATO": {
        "rotulo": "Valor médio do comodato (R$)",
        "ex": "9000",
        "step": "0.01",
        "tipo": "brl",
    },
}
NOVAS_NO_PI6 = {"PCT_COMPRAS_A_PRAZO", "PRAZO_MEDIO_COMODATO", "VALOR_MEDIO_COMODATO"}
NOMES_ALGORITMO = {
    "RandomForestClassifier": "Random Forest",
    "DecisionTreeClassifier": "Árvore de Decisão",
    "LogisticRegression": "Regressão Logística",
}


# ──────────────────────────────────────────────────────────────────────────────
#  APLICAÇÃO
# ──────────────────────────────────────────────────────────────────────────────
def _secret_key() -> str:
    """SECRET_KEY do ambiente; sem ela, uma chave aleatória persistida em
    instance/ — nunca uma chave fixa no código."""
    chave = os.getenv("SECRET_KEY")
    if chave:
        return chave
    caminho = os.path.join(INSTANCE_DIR, "secret_key")
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    if not os.path.exists(caminho):
        with open(caminho, "w") as f:
            f.write(secrets.token_hex(32))
    with open(caminho) as f:
        return f.read().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret_key(),
    PERMANENT_SESSION_LIFETIME=timedelta(
        minutes=int(os.getenv("SESSAO_MINUTOS", "15"))
    ),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SEGURO", "0") == "1",  # 1 em HTTPS
)
login_manager.init_app(app)
app.register_blueprint(auth_bp)


@app.teardown_appcontext
def _fechar_db(_exc):
    db = g.pop("_auth_db", None)
    if db is not None:
        db.close()


@app.before_request
def _exigir_login_no_site():
    if not LOGIN_SITE_INTEIRO or current_user.is_authenticated:
        return None
    if request.endpoint in ("auth.login", "static", "health"):
        return None
    return nao_autorizado()


@app.after_request
def _cabecalhos(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"  # resposta com dado pessoal
    return resp


@app.template_filter("br")
def _fmt_br(valor, casas: int = 0):
    """1234.5 → '1.234,5'. Valor ausente vira '—', nunca 0."""
    if valor is None:
        return "—"
    try:
        txt = f"{float(valor):,.{casas}f}"
    except (TypeError, ValueError):
        return valor
    return txt.replace(",", "§").replace(".", ",").replace("§", ".")


@app.context_processor
def _contexto_global():
    return {
        "csrf_token": csrf_token,
        "fonte_nome": fontes.FONTES.get(ESTADO.fonte, "—"),
    }


# ──────────────────────────────────────────────────────────────────────────────
#  ESTADO: a carga ativa e tudo o que deriva dela, trocados de uma vez
# ──────────────────────────────────────────────────────────────────────────────
def _faixa(prob: float) -> str:
    if prob < FAIXA_BAIXO:
        return "BAIXO"
    if prob < FAIXA_ALTO:
        return "MÉDIO"
    return "ALTO"


class Estado:
    """Imutável depois de criado. Trocar de fonte = criar outro Estado."""

    def __init__(self, carga: fontes.Carga | None = None, fonte=None, erro: str = ""):
        self.carga: Any = carga
        self.fonte = carga.fonte if carga else "local"
        self.leitor = fonte  # objeto da fonte, usado para ler gráficos
        self.erro = erro
        self.pipeline: Any = carga.pipeline if carga else None
        self.card: dict = carga.card if carga else {}
        self.features_num: list[str] = list(self.card.get("features_numericas", []))
        self.features_cat: list[str] = list(self.card.get("features_categoricas", []))
        self.categorias: dict[str, list[str]] = {}
        self.importancias: list[tuple[str, float]] = []
        self.distribuicao: dict[str, int] = {}
        self.carteira: Any = None  # DataFrame indexado por ID_PESSOA, após a carga
        if carga:
            self._derivar(carga)

    def _derivar(self, carga: fontes.Carga) -> None:
        prep = self.pipeline.named_steps["prep"]
        # Categorias lidas do próprio encoder: o formulário nunca oferece um
        # valor que o modelo ignoraria em silêncio.
        enc = prep.named_transformers_["cat"]
        self.categorias = {
            c: [str(v) for v in cats]
            for c, cats in zip(self.features_cat, enc.categories_)
        }

        # Importância por variável original (soma das colunas do one-hot)
        agregado: dict[str, float] = {}
        for nome, valor in zip(
            prep.get_feature_names_out(),
            self.pipeline.named_steps["est"].feature_importances_,
        ):
            base = nome.split("__", 1)[1]
            base = next(
                (c for c in self.features_cat if base.startswith(c + "_")), base
            )
            agregado[base] = agregado.get(base, 0.0) + float(valor)
        self.importancias = sorted(agregado.items(), key=lambda kv: -kv[1])

        df = carga.carteira.copy()
        df.columns = df.columns.str.strip()
        df["ID_PESSOA"] = pd.to_numeric(df["ID_PESSOA"], errors="coerce").astype(
            "Int64"
        )
        df = df.dropna(subset=["ID_PESSOA"]).drop_duplicates("ID_PESSOA")
        df = df.set_index("ID_PESSOA", drop=False)  # índice único: .loc devolve 1 linha
        df["PROB_RISCO"] = self.pipeline.predict_proba(
            df[self.features_num + self.features_cat]
        )[:, 1]
        self.distribuicao = {
            str(k): int(v)
            for k, v in df["PROB_RISCO"].map(_faixa).value_counts().items()
        }
        self.carteira = df

    @property
    def ok(self) -> bool:
        return self.pipeline is not None and self.carteira is not None

    @property
    def algoritmo(self) -> str:
        classe = self.card.get("algoritmo", "")
        return NOMES_ALGORITMO.get(classe, classe or "N/D")


def carregar(nome: str) -> Estado:
    """Carrega uma fonte e devolve o Estado pronto. Levanta FonteIndisponivel."""
    fonte = fontes.criar_fonte(nome)
    carga = fonte.carregar()
    try:
        return Estado(carga, fonte)
    except Exception as e:  # pacote abriu, mas não é o pipeline esperado
        raise fontes.FonteIndisponivel(
            f"Modelo carregado, mas incompatível com o site: {e}"
        ) from e


def _inicializar() -> Estado:
    escolhida = fontes.fonte_escolhida()
    try:
        estado = carregar(escolhida)
        app.logger.info(
            f"Fonte {escolhida}: {estado.algoritmo}, {len(estado.carteira):,} clientes"
        )
        return estado
    except fontes.FonteIndisponivel as e:
        app.logger.error(f"Fonte {escolhida} indisponível: {e}")
        if escolhida == "local":
            return Estado(erro=str(e))
        # Databricks fora do ar não derruba o site: cai para o caminho local
        try:
            estado = carregar("local")
            estado.erro = (
                f"Databricks indisponível na inicialização ({e}). Usando a fonte local."
            )
            return estado
        except fontes.FonteIndisponivel as e2:
            return Estado(erro=f"Databricks: {e} | Local: {e2}")


ESTADO = Estado()


def _features_da_linha(est: Estado, r: Any) -> dict:
    saida: dict[str, Any] = {c: round(float(r[c]), 4) for c in est.features_num}
    saida.update({c: str(r[c]) for c in est.features_cat})
    return saida


def _resultado(est: Estado, prob: float, features: dict) -> dict:
    limiar = float(est.card.get("threshold_classificacao", 0.5))
    return {
        "probabilidade_risco": round(prob, 4),
        "probabilidade_pct": round(prob * 100, 1),
        "nivel_risco": _faixa(prob),
        "classificacao": int(prob >= limiar),
        "threshold_usado": limiar,
        "modelo_utilizado": est.algoritmo,
        "versao_dados": est.card.get("data_version", "N/D"),
        "mcc_holdout": round(est.card.get("metricas_holdout", {}).get("mcc", 0.0), 3),
        "fonte": fontes.FONTES[est.fonte],
        "features": features,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  ROTAS
# ──────────────────────────────────────────────────────────────────────────────
def _painel_empresa(est: Estado) -> dict:
    """Números da Chopp & Cia. Chamado SÓ para usuário autenticado."""
    total = sum(est.distribuicao.values()) or 1
    return {
        **PAINEL_EMPRESA,
        "distribuicao": {
            k: (est.distribuicao.get(k, 0), est.distribuicao.get(k, 0) / total * 100)
            for k in ("BAIXO", "MÉDIO", "ALTO")
        },
        # Pacotes do notebook 08 a partir de out/2026 trazem a matriz medida no
        # holdout; os anteriores caem no valor fixo de PAINEL_EMPRESA.
        "matriz": est.card.get("matriz_holdout") or PAINEL_EMPRESA["matriz"],
        "n_teste": est.card.get("n_holdout") or PAINEL_EMPRESA["n_teste"],
        "n_carteira": 0 if est.carteira is None else len(est.carteira),
        "n_treino": est.card.get("n_treino_final"),
        "prevalencia": est.card.get("prevalencia"),
    }


@app.route("/")
def index():
    est = ESTADO
    return render_template(
        "index.html",
        card=est.card,
        algoritmo=est.algoritmo,
        modelo_ok=est.ok,
        erro_modelo=est.erro if not est.ok else "",
        features_num=est.features_num,
        features_cat=est.features_cat,
        specs=FEATURE_SPECS,
        novas=NOVAS_NO_PI6,
        categorias=est.categorias,
        importancias=est.importancias,
        imp_max=max((v for _, v in est.importancias), default=1.0),
        painel=(
            _painel_empresa(est) if current_user.is_authenticated and est.ok else None
        ),
    )


@app.route("/graficos/<path:nome>")
@login_required
def grafico(nome):
    """Gráficos do EDA: dados da empresa, servidos pela fonte ativa e só com login."""
    est = ESTADO
    if nome not in GRAFICOS_PERMITIDOS or est.leitor is None:
        abort(404)
    try:
        conteudo = est.leitor.ler_grafico(nome)
    except FileNotFoundError:
        abort(404)
    return Response(
        conteudo, mimetype="image/png", headers={"Cache-Control": "private, no-store"}
    )


@app.route("/api/buscar_cliente", methods=["POST"])
@login_required
def buscar_cliente():
    """Body: {"id_pessoa": 1042} → features do cliente (sem score)."""
    est = ESTADO
    try:
        id_pessoa = int((request.get_json(silent=True) or {}).get("id_pessoa", 0))
    except (TypeError, ValueError):
        id_pessoa = 0
    if id_pessoa <= 0:
        return jsonify({"encontrado": False, "erro": "ID inválido."}), 400
    if not est.ok:
        return jsonify({"encontrado": False, "erro": "Carteira não carregada."}), 503

    registrar("CONSULTA_ID", f"ID_PESSOA={id_pessoa}")
    if id_pessoa not in est.carteira.index:
        return jsonify(
            {
                "encontrado": False,
                "id_pessoa": id_pessoa,
                "mensagem": f"ID {id_pessoa} não está na carteira.",
            }
        )
    return jsonify(
        {
            "encontrado": True,
            "id_pessoa": id_pessoa,
            "features": _features_da_linha(est, est.carteira.loc[id_pessoa]),
        }
    )


@app.route("/api/predict", methods=["POST"])
@login_required
def predict():
    """
    Modo 'id'    : {"modo": "id", "id_pessoa": 1042}
    Modo 'manual': {"modo": "manual", "features": {...}}
    """
    est = ESTADO
    if not est.ok:
        return (
            jsonify({"erro": "Modelo ou carteira não carregados. Verifique /health."}),
            503,
        )

    data = request.get_json(silent=True) or {}

    if data.get("modo") == "id":
        try:
            id_pessoa = int(data.get("id_pessoa", 0))
        except (TypeError, ValueError):
            return jsonify({"erro": "ID inválido."}), 400
        if id_pessoa not in est.carteira.index:
            return jsonify({"erro": f"ID {id_pessoa} não encontrado."}), 404

        r = est.carteira.loc[id_pessoa]
        registrar("PREDICAO_ID", f"ID_PESSOA={id_pessoa}")
        res = _resultado(est, float(r["PROB_RISCO"]), _features_da_linha(est, r))
        regra = r.get("ALTO_RISCO_REGRA")
        res.update(
            {
                "modo": "id",
                "id_pessoa": id_pessoa,
                "no_treino": bool(int(r.get("NO_TREINO", 0))),
                "risco_regra": None if pd.isna(regra) else int(regra),
            }
        )
        return jsonify(res)

    # ── Manual: valida tudo antes de chegar ao modelo ─────────────────────────
    feats = data.get("features") or {}
    linha, erros = {}, []
    for c in est.features_num:
        try:
            v = float(feats[c])
        except (KeyError, TypeError, ValueError):
            erros.append(FEATURE_SPECS.get(c, {}).get("rotulo", c))
            continue
        if v < 0 or (c == "PCT_COMPRAS_A_PRAZO" and v > 1):
            erros.append(FEATURE_SPECS.get(c, {}).get("rotulo", c))
            continue
        linha[c] = v
    for c in est.features_cat:
        v = str(feats.get(c, ""))
        if v not in est.categorias.get(c, []):
            erros.append(c)
            continue
        linha[c] = v
    if erros:
        return (
            jsonify({"erro": "Campos ausentes ou inválidos: " + ", ".join(erros)}),
            400,
        )

    prob = float(
        est.pipeline.predict_proba(
            pd.DataFrame([linha])[est.features_num + est.features_cat]
        )[0, 1]
    )
    registrar("SIMULACAO_MANUAL")
    res = _resultado(est, prob, linha)
    res.update({"modo": "manual", "id_pessoa": None})
    return jsonify(res)


# ── Administração da fonte de dados ───────────────────────────────────────────
_ultimo_teste: dict = {}


@app.route("/admin/fonte")
@admin_required
def admin_fonte():
    est = ESTADO
    return render_template(
        "fonte.html",
        estado=est,
        carga=est.carga,
        escolhida=fontes.fonte_escolhida(),
        local=fontes.FonteLocal().descricao(),
        databricks=fontes.resumo_config_databricks(),
        teste=_ultimo_teste,
        compat=compat,
    )


@app.route("/admin/fonte/testar", methods=["POST"])
@admin_required
def admin_fonte_testar():
    """Carrega a fonte SEM ativá-la: mostra se o caminho funciona."""
    global _ultimo_teste
    if not csrf_valido():
        abort(400)
    nome = request.form.get("fonte", "")
    if nome not in fontes.FONTES:
        abort(400)
    try:
        teste = carregar(nome)
        _ultimo_teste = {
            "fonte": nome,
            "ok": True,
            "clientes": len(teste.carteira),
            "algoritmo": teste.algoritmo,
            "versao": teste.card.get("data_version"),
            "segundos": teste.carga.segundos,
            "avisos": teste.carga.avisos,
        }
    except fontes.FonteIndisponivel as e:
        _ultimo_teste = {"fonte": nome, "ok": False, "erro": str(e)}
    registrar("TESTE_FONTE", f"{nome}: {'ok' if _ultimo_teste['ok'] else 'falhou'}")
    return redirect(url_for("admin_fonte"))


@app.route("/admin/fonte/ativar", methods=["POST"])
@admin_required
def admin_fonte_ativar():
    """Troca a fonte ativa. Se a nova falhar, a atual continua no ar."""
    global ESTADO
    if not csrf_valido():
        abort(400)
    nome = request.form.get("fonte", "")
    if nome not in fontes.FONTES:
        abort(400)
    try:
        novo = carregar(nome)
    except fontes.FonteIndisponivel as e:
        registrar("TROCA_FONTE", f"{nome}: falhou — mantida {ESTADO.fonte}")
        flash(
            f"Não foi possível ativar {fontes.FONTES[nome]}: {e} "
            f"O site continua usando {fontes.FONTES[ESTADO.fonte]}.",
            "danger",
        )
        return redirect(url_for("admin_fonte"))
    ESTADO = novo
    fontes.gravar_fonte(nome, current_user.username)
    registrar("TROCA_FONTE", f"{nome}: ativada ({len(novo.carteira):,} clientes)")
    flash(
        f"Fonte {fontes.FONTES[nome]} ativada: {len(novo.carteira):,} clientes, "
        f"{novo.algoritmo}, DATA_VERSION {novo.card.get('data_version')}.",
        "success",
    )
    return redirect(url_for("admin_fonte"))


@app.route("/health")
def health():
    est = ESTADO
    return jsonify(
        {
            "status": "ok" if est.ok else "degradado",
            "fonte": est.fonte,
            "modelo_carregado": est.pipeline is not None,
            "carteira_carregada": est.carteira is not None,
            "algoritmo": est.card.get("algoritmo", "N/D"),
            "data_version": est.card.get("data_version", "N/D"),
        }
    )


application = app

ESTADO = _inicializar()
if init_db() == 0:
    app.logger.warning(
        "Nenhum usuário cadastrado. Crie o primeiro com: "
        "python gerenciar_usuarios.py criar admin --admin"
    )

if __name__ == "__main__":
    app.run(host="localhost", port=5000, debug=True, use_reloader=False)
