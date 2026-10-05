"""
auth.py — Autenticação e trilha de auditoria (LGPD)
PI6 - FATEC Votorantim | 2026

Base: login (Flask-Login + hash de senha do Werkzeug + SQLite),
adaptado ao que a LGPD pede de um sistema que exibe perfil de risco por cliente:

  • acesso só autenticado aos dados por ID_PESSOA        (art. 46 — segurança)
  • registro de quem consultou o quê, e quando           (art. 37 — registro das operações)
  • retenção limitada do próprio log                     (art. 15/16 — término do tratamento)
  • bloqueio temporário após tentativas seguidas         (art. 46 — acesso não autorizado)

Sem cadastro público: usuários são criados pelo administrador com
`python gerenciar_usuarios.py criar <usuario>`.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.getenv("AUTH_DB_PATH", os.path.join(INSTANCE_DIR, "usuarios.db"))

MAX_TENTATIVAS = int(os.getenv("LOGIN_MAX_TENTATIVAS", "5"))
MINUTOS_BLOQUEIO = int(os.getenv("LOGIN_MINUTOS_BLOQUEIO", "15"))
LOG_RETENCAO_DIAS = int(os.getenv("LOG_RETENCAO_DIAS", "180"))
SENHA_MIN = 8

auth_bp = Blueprint("auth", __name__)

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Faça login para acessar os dados de clientes."
login_manager.login_message_category = "warning"

# Hash usado quando o usuário não existe: a verificação custa o mesmo tempo nos
# dois casos, e o tempo de resposta não revela quais usuários estão cadastrados.
_HASH_FICTICIO = generate_password_hash(secrets.token_hex(16))

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash     TEXT NOT NULL,
    perfil            TEXT NOT NULL DEFAULT 'analista'
                      CHECK (perfil IN ('admin', 'analista')),
    ativo             INTEGER NOT NULL DEFAULT 1,
    tentativas_falhas INTEGER NOT NULL DEFAULT 0,
    bloqueado_ate     TEXT,
    criado_em         TEXT NOT NULL,
    ultimo_login      TEXT
);
CREATE TABLE IF NOT EXISTS log_acesso (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    quando   TEXT NOT NULL,
    username TEXT,
    evento   TEXT NOT NULL,
    detalhe  TEXT,
    ip       TEXT
);
CREATE INDEX IF NOT EXISTS ix_log_quando ON log_acesso (quando);
"""


# ──────────────────────────────────────────────────────────────────────────────
#  BANCO
# ──────────────────────────────────────────────────────────────────────────────
def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def conectar(caminho: str = DB_PATH) -> sqlite3.Connection:
    """Conexão avulsa — usada pelo CLI e na inicialização."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    return conn


def get_db() -> sqlite3.Connection:
    """Conexão por request, fechada no teardown."""
    if "_auth_db" not in g:
        g._auth_db = conectar()
    return g._auth_db


def init_db(caminho: str = DB_PATH) -> int:
    """Cria as tabelas, aplica a retenção do log e devolve o nº de usuários."""
    conn = conectar(caminho)
    try:
        conn.executescript(SCHEMA)
        limite = (datetime.now() - timedelta(days=LOG_RETENCAO_DIAS)).isoformat(
            timespec="seconds"
        )
        conn.execute("DELETE FROM log_acesso WHERE quando < ?", (limite,))
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
    finally:
        conn.close()


def registrar(evento: str, detalhe: str = "", username: str | None = None) -> None:
    """Trilha de auditoria. Nunca derruba a requisição por falha de log."""
    try:
        if username is None and current_user.is_authenticated:
            username = current_user.username
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        db = get_db()
        db.execute(
            "INSERT INTO log_acesso (quando, username, evento, detalhe, ip) "
            "VALUES (?, ?, ?, ?, ?)",
            (_agora(), username, evento, detalhe, ip.split(",")[0].strip()),
        )
        db.commit()
    except Exception as e:  # pragma: no cover
        current_app.logger.error(f"Falha ao registrar auditoria: {e}")


# ──────────────────────────────────────────────────────────────────────────────
#  USUÁRIO
# ──────────────────────────────────────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, row: sqlite3.Row):
        self.id = row["id"]
        self.username = row["username"]
        self.perfil = row["perfil"]
        self._ativo = bool(row["ativo"])

    @property
    def is_active(self) -> bool:
        return self._ativo

    @property
    def is_admin(self) -> bool:
        return self.perfil == "admin"

    @staticmethod
    def get(user_id) -> "User | None":
        row = (
            get_db()
            .execute("SELECT * FROM usuarios WHERE id = ?", (user_id,))
            .fetchone()
        )
        return User(row) if row else None


@login_manager.user_loader
def load_user(user_id):
    try:
        return User.get(int(user_id))
    except (TypeError, ValueError):
        return None


@login_manager.unauthorized_handler
def nao_autorizado():
    # Chamadas da API recebem JSON; navegação recebe a tela de login.
    if request.path.startswith("/api/"):
        return (
            jsonify({"erro": "Sessão expirada ou não autenticada.", "login": True}),
            401,
        )
    flash(login_manager.login_message, login_manager.login_message_category)
    return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def _destino_seguro(destino: str | None) -> str:
    """Só aceita caminho relativo do próprio site (evita open redirect)."""
    if destino:
        p = urlparse(destino)
        if (
            not p.scheme
            and not p.netloc
            and destino.startswith("/")
            and not destino.startswith("//")
        ):
            return destino
    return url_for("index")


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def csrf_valido() -> bool:
    """Confere o token de um formulário POST com o da sessão."""
    enviado = request.form.get("csrf_token", "")
    esperado = session.get("_csrf", "")
    return bool(esperado) and secrets.compare_digest(enviado, esperado)


# ──────────────────────────────────────────────────────────────────────────────
#  ROTAS
# ──────────────────────────────────────────────────────────────────────────────
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_destino_seguro(request.args.get("next")))

    if request.method == "POST":
        if request.form.get("csrf_token") != session.get("_csrf"):
            flash("Formulário expirado. Tente novamente.", "warning")
            return redirect(url_for("auth.login"))

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        row = db.execute(
            "SELECT * FROM usuarios WHERE username = ?", (username,)
        ).fetchone()

        # Conta bloqueada temporariamente por excesso de tentativas
        if row and row["bloqueado_ate"] and row["bloqueado_ate"] > _agora():
            check_password_hash(_HASH_FICTICIO, password)
            registrar("LOGIN_BLOQUEADO", "tentativa durante bloqueio", username)
            flash(
                f"Acesso bloqueado temporariamente por excesso de tentativas. "
                f"Tente novamente em até {MINUTOS_BLOQUEIO} minutos.",
                "danger",
            )
            return redirect(url_for("auth.login"))

        senha_ok = check_password_hash(
            row["password_hash"] if row else _HASH_FICTICIO, password
        )

        if row and senha_ok and row["ativo"]:
            db.execute(
                "UPDATE usuarios SET tentativas_falhas = 0, bloqueado_ate = NULL, "
                "ultimo_login = ? WHERE id = ?",
                (_agora(), row["id"]),
            )
            db.commit()
            session.clear()  # nova sessão a cada login (evita session fixation)
            login_user(User(row))
            session.permanent = True
            registrar("LOGIN_OK", "", row["username"])
            return redirect(_destino_seguro(request.args.get("next")))

        if row:
            falhas = row["tentativas_falhas"] + 1
            bloqueio = None
            if falhas >= MAX_TENTATIVAS:
                bloqueio = (
                    datetime.now() + timedelta(minutes=MINUTOS_BLOQUEIO)
                ).isoformat(timespec="seconds")
                falhas = 0
            db.execute(
                "UPDATE usuarios SET tentativas_falhas = ?, bloqueado_ate = ? WHERE id = ?",
                (falhas, bloqueio, row["id"]),
            )
            db.commit()
            motivo = (
                "usuário inativo"
                if (senha_ok and not row["ativo"])
                else "senha inválida"
            )
            registrar(
                "LOGIN_FALHA",
                motivo + (" — conta bloqueada" if bloqueio else ""),
                username,
            )
        else:
            registrar("LOGIN_FALHA", "usuário inexistente", username[:64])

        # Mensagem única: não revela se o usuário existe
        flash("Usuário ou senha inválidos.", "danger")
        return redirect(url_for("auth.login", next=request.args.get("next")))

    return render_template("login.html", csrf_token=csrf_token())


@auth_bp.route("/logout")
@login_required
def logout():
    registrar("LOGOUT")
    logout_user()
    session.clear()
    flash("Você saiu com sucesso.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/auditoria")
@admin_required
def auditoria():
    db = get_db()
    eventos = db.execute(
        "SELECT quando, username, evento, detalhe, ip FROM log_acesso "
        "ORDER BY id DESC LIMIT 300"
    ).fetchall()
    usuarios = db.execute(
        "SELECT username, perfil, ativo, criado_em, ultimo_login, bloqueado_ate "
        "FROM usuarios ORDER BY username"
    ).fetchall()
    registrar("ACESSO_AUDITORIA")
    return render_template(
        "auditoria.html",
        eventos=eventos,
        usuarios=usuarios,
        retencao=LOG_RETENCAO_DIAS,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  OPERAÇÕES DE ADMINISTRAÇÃO (usadas pelo gerenciar_usuarios.py)
# ──────────────────────────────────────────────────────────────────────────────
def validar_senha(senha: str) -> None:
    if len(senha) < SENHA_MIN:
        raise ValueError(f"A senha precisa ter ao menos {SENHA_MIN} caracteres.")
    if senha.isdigit() or senha.isalpha():
        raise ValueError("Use letras e números na senha.")


def criar_usuario(conn, username: str, senha: str, perfil: str = "analista") -> None:
    validar_senha(senha)
    conn.execute(
        "INSERT INTO usuarios (username, password_hash, perfil, criado_em) VALUES (?, ?, ?, ?)",
        (username.strip(), generate_password_hash(senha), perfil, _agora()),
    )
    conn.commit()


def trocar_senha(conn, username: str, senha: str) -> bool:
    validar_senha(senha)
    cur = conn.execute(
        "UPDATE usuarios SET password_hash = ?, tentativas_falhas = 0, bloqueado_ate = NULL "
        "WHERE username = ?",
        (generate_password_hash(senha), username),
    )
    conn.commit()
    return cur.rowcount > 0
