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
    page_title="Salute — Relatórios",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================
# Visual customizado — paleta moderna
# =============================================================
st.markdown("""
<style>
    /* Tipografia e fundo */
    .main {
        background-color: #FAFBFC;
    }

    /* Título principal mais elegante */
    h1 {
        color: #1E3A5F;
        font-weight: 700;
        letter-spacing: -0.5px;
        padding-bottom: 0.5rem;
        border-bottom: 3px solid #1E5BA8;
        margin-bottom: 1.5rem !important;
    }

    h2, h3 {
        color: #2C3E50;
        font-weight: 600;
    }

    /* Sidebar mais bonita */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1E3A5F 0%, #2C5282 100%);
    }
    [data-testid="stSidebar"] * {
        color: #FFFFFF;
    }
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] .stCheckbox label,
    [data-testid="stSidebar"] .stCaption {
        color: #E2E8F0 !important;
    }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {
        color: #FFFFFF !important;
        border-bottom: none;
    }
    /* Inputs na sidebar */
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] [data-baseweb="input"] {
        background-color: #FFFFFF !important;
        color: #1E3A5F !important;
    }

    /* Cards de métrica mais estilizados */
    [data-testid="stMetric"] {
        background-color: #FFFFFF;
        padding: 1rem 1.2rem;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        border: 1px solid #E2E8F0;
        transition: transform 0.2s, box-shadow 0.2s;
    }
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.85rem !important;
        color: #64748B !important;
        font-weight: 500;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.6rem !important;
        color: #1E3A5F !important;
        font-weight: 700;
    }

    /* Abas mais bonitas */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: transparent;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #F1F5F9;
        border-radius: 8px 8px 0 0;
        padding: 8px 16px;
        font-weight: 500;
    }
    .stTabs [aria-selected="true"] {
        background-color: #1E5BA8 !important;
        color: white !important;
    }

    /* Botões */
    .stButton button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s;
    }
    .stButton button:hover {
        transform: translateY(-1px);
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }

    /* Dataframes mais limpos */
    [data-testid="stDataFrame"] {
        border: 1px solid #E2E8F0;
        border-radius: 8px;
    }

    /* Expander */
    .streamlit-expanderHeader {
        background-color: #F8FAFC;
        border-radius: 8px;
    }

    /* Caption mais sutil */
    .stCaption, [data-testid="stCaptionContainer"] {
        color: #64748B;
        font-style: normal;
    }

    /* Divider mais sutil */
    hr {
        margin: 1.5rem 0 !important;
        border-color: #E2E8F0;
    }
</style>
""", unsafe_allow_html=True)

# =============================================================
# Sidebar — filtros globais
# =============================================================
with st.sidebar:
    st.markdown("# 🛡️ Salute")
    st.markdown("##### Relatórios consolidados")
    st.caption("3C Plus · Exact · SGCor")

    st.divider()

    st.markdown("### 📅 Período")
    hoje = date.today()
    data_inicio = st.date_input("Data início", value=hoje - timedelta(days=30))
    data_fim = st.date_input("Data fim", value=hoje)

    if data_inicio > data_fim:
        st.error("Data início precisa ser <= data fim")
        st.stop()

    st.divider()
    st.markdown("### ⚙️ Cache")
    usar_cache = st.checkbox("Usar cache (mais rápido)", value=True)
    ttl = st.slider("TTL (minutos)", 1, 240, 60)

    if st.button("🗑️ Limpar cache", use_container_width=True):
        cache.limpar()
        st.success("Cache limpo")

    st.divider()
    st.caption("💡 Dados atualizados via API")


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
st.markdown(
    f"""
    <div style='padding: 0.5rem 0 1.5rem 0;'>
        <h1 style='margin: 0;'>📊 Dashboard de Performance</h1>
        <p style='color: #64748B; margin: 0.25rem 0 0 0; font-size: 1rem;'>
            Período: <strong style='color: #1E5BA8;'>{data_inicio.strftime('%d/%m/%Y')}</strong>
            até <strong style='color: #1E5BA8;'>{data_fim.strftime('%d/%m/%Y')}</strong>
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

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
