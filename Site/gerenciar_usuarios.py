"""
gerenciar_usuarios.py — Administração dos usuários do site

Não há cadastro pelo site: quem acessa dados de clientes é autorizado pelo
administrador. Uso:

    python gerenciar_usuarios.py criar <usuario> [--admin]
    python gerenciar_usuarios.py senha <usuario>
    python gerenciar_usuarios.py desativar <usuario>
    python gerenciar_usuarios.py ativar <usuario>
    python gerenciar_usuarios.py desbloquear <usuario>
    python gerenciar_usuarios.py listar

A senha é pedida no terminal, sem eco. Para automação, defina SENHA_USUARIO.
"""

import argparse
import getpass
import os
import sqlite3
import sys

from auth import DB_PATH, conectar, criar_usuario, init_db, trocar_senha


def _ler_senha() -> str:
    senha = os.getenv("SENHA_USUARIO")
    if senha:
        return senha
    senha = getpass.getpass("Senha: ")
    if senha != getpass.getpass("Confirme a senha: "):
        sys.exit("As senhas não conferem.")
    return senha


def _atualizar(conn, username: str, sql: str, msg: str) -> None:
    cur = conn.execute(sql, (username,))
    conn.commit()
    print(msg if cur.rowcount else f"Usuário '{username}' não encontrado.")


def main() -> None:
    p = argparse.ArgumentParser(description="Usuários do site Chopp & Cia (PI6)")
    sub = p.add_subparsers(dest="acao", required=True)

    c = sub.add_parser("criar", help="cria um usuário")
    c.add_argument("usuario")
    c.add_argument(
        "--admin", action="store_true", help="pode ver a trilha de auditoria"
    )
    for nome in ("senha", "desativar", "ativar", "desbloquear"):
        sub.add_parser(nome).add_argument("usuario")
    sub.add_parser("listar")

    args = p.parse_args()
    init_db()
    conn = conectar()
    try:
        if args.acao == "criar":
            perfil = "admin" if args.admin else "analista"
            criar_usuario(conn, args.usuario, _ler_senha(), perfil)
            print(f"Usuário '{args.usuario}' criado ({perfil}).")
        elif args.acao == "senha":
            ok = trocar_senha(conn, args.usuario, _ler_senha())
            print(
                "Senha alterada." if ok else f"Usuário '{args.usuario}' não encontrado."
            )
        elif args.acao == "desativar":
            _atualizar(
                conn,
                args.usuario,
                "UPDATE usuarios SET ativo = 0 WHERE username = ?",
                "Usuário desativado.",
            )
        elif args.acao == "ativar":
            _atualizar(
                conn,
                args.usuario,
                "UPDATE usuarios SET ativo = 1 WHERE username = ?",
                "Usuário ativado.",
            )
        elif args.acao == "desbloquear":
            _atualizar(
                conn,
                args.usuario,
                "UPDATE usuarios SET tentativas_falhas = 0, bloqueado_ate = NULL "
                "WHERE username = ?",
                "Usuário desbloqueado.",
            )
        elif args.acao == "listar":
            linhas = conn.execute(
                "SELECT username, perfil, ativo, ultimo_login FROM usuarios ORDER BY username"
            ).fetchall()
            print(f"Banco: {DB_PATH}")
            if not linhas:
                print("Nenhum usuário cadastrado.")
            for r in linhas:
                print(
                    f"  {r['username']:<20} {r['perfil']:<9} "
                    f"{'ativo' if r['ativo'] else 'INATIVO':<8} "
                    f"último login: {r['ultimo_login'] or '—'}"
                )
    except ValueError as e:
        sys.exit(str(e))
    except sqlite3.IntegrityError:
        sys.exit(f"O usuário '{args.usuario}' já existe.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
