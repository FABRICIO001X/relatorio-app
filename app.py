"""
Dashboard Salute — 3C Plus + Exact + SGCor.

Rodar com:
    streamlit run app.py
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from db import cache

load_dotenv()

st.set_page_config(
    page_title="Salute — Relatórios",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================
# Estilo
# =============================================================
st.markdown("""
<style>
    /* ---------- Base ---------- */
    .stApp { background-color: #F7F9FC; }

    .block-container { padding-top: 2.2rem; max-width: 1400px; }

    h1, h2, h3, h4 { color: #16314F; font-weight: 650; letter-spacing: -0.2px; }
    h3 { font-size: 1.35rem; margin-bottom: .2rem; }

    /* ---------- Sidebar ---------- */
    [data-testid="stSidebar"] {
        background: linear-gradient(170deg, #16314F 0%, #1E5BA8 100%);
        border-right: none;
    }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] h4,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] span[data-testid="stMarkdownContainer"] {
        color: #FFFFFF !important;
    }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
        color: #C7D9F0 !important;
    }
    /* Campos precisam de fundo claro e texto escuro para serem legíveis */
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] [data-baseweb="input"] > div,
    [data-testid="stSidebar"] [data-baseweb="select"] > div {
        background-color: #FFFFFF !important;
        color: #16314F !important;
        border-radius: 8px !important;
        border: none !important;
    }
    [data-testid="stSidebar"] input { color: #16314F !important; }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.18); }
    [data-testid="stSidebar"] .stButton button {
        background-color: rgba(255,255,255,.12);
        color: #FFFFFF;
        border: 1px solid rgba(255,255,255,.35);
    }
    [data-testid="stSidebar"] .stButton button:hover {
        background-color: rgba(255,255,255,.22);
        border-color: #FFFFFF;
    }

    /* ---------- Cards de métrica ---------- */
    [data-testid="stMetric"] {
        background: #FFFFFF;
        padding: 1rem 1.1rem;
        border-radius: 14px;
        border: 1px solid #E4EAF2;
        box-shadow: 0 1px 2px rgba(16,49,79,.04);
        transition: box-shadow .18s ease, transform .18s ease;
    }
    [data-testid="stMetric"]:hover {
        box-shadow: 0 6px 18px rgba(16,49,79,.10);
        transform: translateY(-2px);
    }
    [data-testid="stMetricLabel"] p {
        font-size: .82rem !important;
        color: #64748B !important;
        font-weight: 500;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.7rem !important;
        color: #16314F !important;
        font-weight: 700;
    }

    /* ---------- Abas ---------- */
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        border-bottom: 1px solid #E4EAF2;
        padding-bottom: 2px;
    }
    .stTabs [data-baseweb="tab"] {
        background: #FFFFFF;
        border: 1px solid #E4EAF2;
        border-radius: 10px 10px 0 0;
        padding: 9px 18px;
        font-weight: 550;
        color: #47617D;
    }
    .stTabs [aria-selected="true"] {
        background: #1E5BA8 !important;
        border-color: #1E5BA8 !important;
        color: #FFFFFF !important;
    }

    /* ---------- Tabelas ---------- */
    [data-testid="stDataFrame"] {
        border: 1px solid #E4EAF2;
        border-radius: 12px;
        overflow: hidden;
    }

    /* ---------- Botões da área principal ---------- */
    .stDownloadButton button, .block-container .stButton button {
        border-radius: 9px;
        font-weight: 550;
        border: 1px solid #CBD8E8;
    }

    /* ---------- Expander ---------- */
    [data-testid="stExpander"] {
        border: 1px solid #E4EAF2;
        border-radius: 12px;
        background: #FFFFFF;
    }

    hr { margin: 1.4rem 0 !important; border-color: #E4EAF2; }
</style>
""", unsafe_allow_html=True)

# =============================================================
# Sidebar
# =============================================================
with st.sidebar:
    st.markdown("# 🛡️ Salute")
    st.caption("Relatórios consolidados · 3C Plus · Exact · SGCor")

    st.divider()

    st.markdown("### 📅 Período")
    hoje = date.today()

    atalho = st.radio(
        "Atalhos",
        ["Últimos 7 dias", "Últimos 30 dias", "Este mês", "Personalizado"],
        index=1,
        label_visibility="collapsed",
    )

    if atalho == "Últimos 7 dias":
        ini_pad, fim_pad = hoje - timedelta(days=6), hoje
    elif atalho == "Últimos 30 dias":
        ini_pad, fim_pad = hoje - timedelta(days=29), hoje
    elif atalho == "Este mês":
        ini_pad, fim_pad = hoje.replace(day=1), hoje
    else:
        ini_pad, fim_pad = hoje - timedelta(days=29), hoje

    if atalho == "Personalizado":
        data_inicio = st.date_input("Data início", value=ini_pad, format="DD/MM/YYYY")
        data_fim = st.date_input("Data fim", value=fim_pad, format="DD/MM/YYYY")
    else:
        data_inicio, data_fim = ini_pad, fim_pad
        st.caption(
            f"De {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
        )

    if data_inicio > data_fim:
        st.error("Data início precisa ser menor ou igual à data fim")
        st.stop()

    st.divider()
    st.markdown("### ⚙️ Dados")
    usar_cache = st.checkbox("Usar cache (mais rápido)", value=True)
    ttl = st.slider("Validade do cache (min)", 1, 240, 60)

    if st.button("🔄 Atualizar dados", use_container_width=True):
        cache.limpar()
        st.success("Cache limpo — recarregando")
        st.rerun()

# =============================================================
# Cabeçalho
# =============================================================
st.markdown(
    f"""
    <div style='padding: 0 0 1.2rem 0;'>
        <h1 style='margin:0; font-size:2rem;'>Dashboard de Performance</h1>
        <p style='color:#64748B; margin:.3rem 0 0 0;'>
            Período analisado:
            <strong style='color:#1E5BA8;'>{data_inicio.strftime('%d/%m/%Y')}</strong>
            a <strong style='color:#1E5BA8;'>{data_fim.strftime('%d/%m/%Y')}</strong>
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_visao, tab_sdrs, tab_3c, tab_exact, tab_com, tab_sgcor = st.tabs(
    ["📈 Visão geral", "👥 SDRs", "📞 3C Plus", "🎯 Consultoras",
     "💰 Comissões BDR", "📑 SGCor"]
)

params = dict(
    data_inicio=data_inicio,
    data_fim=data_fim,
    ttl_minutos=ttl,
    usar_cache=usar_cache,
)

with tab_visao:
    from relatorios import visao_geral
    visao_geral.renderizar(**params)

with tab_sdrs:
    from relatorios import sdr_performance
    sdr_performance.renderizar(**params)

with tab_3c:
    from relatorios import tres_c_plus_performance
    tres_c_plus_performance.renderizar(**params)

with tab_exact:
    from relatorios import exact_consultoras
    exact_consultoras.renderizar(**params)

with tab_com:
    from relatorios import comissoes_bdr
    comissoes_bdr.renderizar(**params)

with tab_sgcor:
    from relatorios import sgcor_dashboard
    sgcor_dashboard.renderizar(**params)
