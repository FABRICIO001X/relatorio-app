"""
Login e controle de acesso.

As senhas NUNCA ficam neste arquivo — o repositório é público.
Elas vão nos Secrets do Streamlit (painel do app > Settings > Secrets),
no formato:

    [login.fabricio]
    senha = "..."
    perfil = "admin"

    [login.iasmim]
    senha = "..."
    perfil = "bdr"
    bdr = "IASMIM"

Perfis:
- admin → acesso a todas as abas
- bdr   → acesso apenas à Meta semanal
"""
from __future__ import annotations

import hmac

import streamlit as st

PERFIL_ADMIN = "admin"
PERFIL_BDR = "bdr"


def _config_usuarios() -> dict:
    """Lê os usuários dos Secrets. Retorna {} se não estiver configurado."""
    try:
        return dict(st.secrets.get("login", {}))
    except Exception:
        return {}


def _validar(usuario: str, senha: str) -> dict | None:
    """Confere as credenciais e devolve os dados do usuário, ou None."""
    usuarios = _config_usuarios()
    dados = usuarios.get(usuario.strip().lower())
    if not dados:
        return None
    esperada = str(dados.get("senha", ""))
    if not esperada:
        return None
    # compare_digest evita vazar informação pelo tempo de resposta
    if not hmac.compare_digest(senha, esperada):
        return None
    return {
        "usuario": usuario.strip().lower(),
        "perfil": dados.get("perfil", PERFIL_BDR),
        "bdr": dados.get("bdr"),
        "nome": dados.get("nome") or usuario.strip().title(),
    }


def _tela_login():
    st.markdown(
        """
        <div style='text-align:center; padding:2.5rem 0 1rem 0;'>
            <div style='font-size:3rem;'>🛡️</div>
            <h1 style='margin:.3rem 0 0 0;'>Salute</h1>
            <p style='color:#64748B; margin:.2rem 0 0 0;'>Relatórios internos</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _, meio, _ = st.columns([1, 1.4, 1])
    with meio:
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        entrar = st.button("Entrar", use_container_width=True, type="primary")

        if entrar:
            if not _config_usuarios():
                st.error(
                    "Nenhum usuário configurado. Adicione a seção [login] nos "
                    "Secrets do app."
                )
                return
            dados = _validar(usuario, senha)
            if dados:
                st.session_state["auth"] = dados
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")


def exigir_login() -> dict:
    """Bloqueia o app até o login. Devolve os dados do usuário autenticado."""
    if "auth" in st.session_state:
        return st.session_state["auth"]
    _tela_login()
    st.stop()


def sair():
    st.session_state.pop("auth", None)
    st.rerun()


def eh_admin(auth: dict) -> bool:
    return auth.get("perfil") == PERFIL_ADMIN
