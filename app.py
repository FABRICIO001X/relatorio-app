"""
Dashboard de relatórios — 3C Plus + Exact + SGCor.

Rodar com:
    streamlit run app.py
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from connectors.exact import ExactClient, ExactError
from connectors.sgcor import SGCorClient, SGCorError
from connectors.tres_c_plus import TresCPlusClient, TresCPlusError
from db import cache

load_dotenv()

st.set_page_config(
    page_title="Relatório Unificado",
    page_icon="📊",
    layout="wide",
)

# =============================================================
# Sidebar — filtros globais
# =============================================================
with st.sidebar:
    st.title("📊 Relatório")
    st.caption("3C Plus · Exact · SGCor")

    hoje = date.today()
    data_inicio = st.date_input("Data início", value=hoje - timedelta(days=30))
    data_fim = st.date_input("Data fim", value=hoje)

    if data_inicio > data_fim:
        st.error("Data início precisa ser <= data fim")
        st.stop()

    st.divider()
    usar_cache = st.checkbox("Usar cache (mais rápido)", value=True)
    ttl = st.slider("TTL do cache (minutos)", 1, 240, 60)

    if st.button("🗑️ Limpar cache"):
        cache.limpar()
        st.success("Cache limpo")

    st.divider()
    st.caption("Edite o arquivo `.env` com suas credenciais.")


# =============================================================
# Helpers
# =============================================================
def carregar_com_cache(chave: str, fetch_fn, ttl_min: int) -> pd.DataFrame:
    """Tenta o cache; se não tiver, busca da API e salva."""
    if usar_cache:
        df = cache.buscar_df(chave, ttl_segundos=ttl_min * 60)
        if df is not None:
            return df
    dados = fetch_fn()
    df = pd.DataFrame(dados) if not isinstance(dados, pd.DataFrame) else dados
    cache.salvar_df(chave, df)
    return df


def mostrar_erro_amigavel(titulo: str, erro: Exception):
    st.error(f"❌ {titulo}")
    with st.expander("Detalhes técnicos"):
        st.code(str(erro))
    st.info(
        "Verifique:\n"
        "- Se as credenciais no `.env` estão corretas\n"
        "- Se o endpoint/path no conector bate com a doc atual\n"
        "- Se a sua conta tem permissão pra esse recurso"
    )


# =============================================================
# Tabs
# =============================================================
st.title("Relatório consolidado")
tab_visao, tab_sdrs, tab_3c, tab_exact, tab_sgcor = st.tabs(
    ["📈 Visão geral", "👥 SDRs", "📞 3C Plus", "🎯 Exact", "📑 SGCor"]
)

# -------------------------------------------------------------
# SDRs (relatório customizado)
# -------------------------------------------------------------
with tab_sdrs:
    from relatorios import sdr_performance
    sdr_performance.renderizar(
        data_inicio=data_inicio,
        data_fim=data_fim,
        ttl_minutos=ttl,
        usar_cache=usar_cache,
    )

# -------------------------------------------------------------
# 3C PLUS
# -------------------------------------------------------------
with tab_3c:
    from relatorios import tres_c_plus_performance
    tres_c_plus_performance.renderizar(
        data_inicio=data_inicio,
        data_fim=data_fim,
        ttl_minutos=ttl,
        usar_cache=usar_cache,
    )

# -------------------------------------------------------------
# EXACT
# -------------------------------------------------------------
with tab_exact:
    from relatorios import exact_consultoras
    exact_consultoras.renderizar(
        data_inicio=data_inicio,
        data_fim=data_fim,
        ttl_minutos=ttl,
        usar_cache=usar_cache,
    )

# -------------------------------------------------------------
# SGCOR
# -------------------------------------------------------------
with tab_sgcor:
    from relatorios import sgcor_dashboard
    sgcor_dashboard.renderizar(
        data_inicio=data_inicio,
        data_fim=data_fim,
        ttl_minutos=ttl,
        usar_cache=usar_cache,
    )

# -------------------------------------------------------------
# VISÃO GERAL
# -------------------------------------------------------------
with tab_visao:
    st.subheader("KPIs consolidados")
    st.caption("Esta aba puxa do cache das outras abas — visite-as primeiro.")

    col1, col2, col3 = st.columns(3)

    df = cache.buscar_df(f"3cplus:calls:{data_inicio}:{data_fim}", ttl_segundos=ttl * 60)
    col1.metric("Chamadas 3C Plus", len(df) if df is not None else "—")

    # Tenta achar a visão Exact mais recente em cache (qualquer uma)
    df_exact_cache = None
    for v in [
        "Leads (todos)", "Histórico de ligações", "Reuniões",
        "Leads vendidos", "Leads descartados",
    ]:
        df_exact_cache = cache.buscar_df(
            f"exact:{v}:{data_inicio}:{data_fim}", ttl_segundos=ttl * 60
        )
        if df_exact_cache is not None:
            col2.metric(f"Exact: {v}", len(df_exact_cache))
            break
    else:
        col2.metric("Exact", "—")

    # SGCor: agrupar todos os tipos de relatório que foram subidos
    sgcor_total = 0
    sgcor_tipos = 0
    for tipo in [
        "Propostas / Apólices", "Sinistros", "Comissões / Repasses",
        "Pagamentos", "Clientes inadimplentes", "Produção", "Renovações", "Outro",
    ]:
        df_t = cache.buscar_df(f"sgcor:{tipo}", ttl_segundos=ttl * 60)
        if df_t is not None:
            sgcor_total += len(df_t)
            sgcor_tipos += 1
    col3.metric(
        "SGCor (registros)",
        sgcor_total if sgcor_tipos else "—",
        help=f"{sgcor_tipos} tipo(s) de relatório carregado(s)" if sgcor_tipos else None,
    )

    st.divider()
    st.markdown(
        """
        **Status dos conectores:**
        - ✅ **3C Plus** — API confirmada (`app.3c.fluxoti.com/v1`, Bearer token)
        - ✅ **Exact Sales** — API confirmada (`api.exactspotter.com/v3`, header `token_exact`, OData)
        - 🚧 **SGCor** — modo upload manual (CSV/XLSX). API será integrada quando disponível.

        **Próximos passos:**
        1. Configurar tokens no `.env` e testar 3C Plus / Exact
        2. Exportar relatórios do SGCor e subir na aba correspondente
        3. Quando a API do SGCor chegar, atualizar `connectors/sgcor.py` mantendo a mesma interface
        """
    )
