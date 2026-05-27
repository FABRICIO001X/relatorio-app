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
    st.subheader("3C Plus — Chamadas")
    chave_3c = f"3cplus:calls:{data_inicio}:{data_fim}"
    try:
        cliente_3c = TresCPlusClient()

        def fetch_3c():
            resp = cliente_3c.listar_chamadas(data_inicio, data_fim)
            # APIs costumam envelopar em {"data": [...]} ou {"calls": [...]}
            if isinstance(resp, dict):
                for k in ("data", "calls", "items", "results"):
                    if k in resp and isinstance(resp[k], list):
                        return resp[k]
                return [resp]
            return resp

        df_3c = carregar_com_cache(chave_3c, fetch_3c, ttl)
        if df_3c.empty:
            st.info("Nenhuma chamada no período.")
        else:
            col1, col2, col3 = st.columns(3)
            col1.metric("Total de chamadas", len(df_3c))
            if "duration" in df_3c.columns:
                col2.metric("Duração média (s)", f"{df_3c['duration'].mean():.0f}")
            if "status" in df_3c.columns:
                atendidas = (df_3c["status"] == "answered").sum()
                col3.metric("Atendidas", atendidas)

            st.dataframe(df_3c, use_container_width=True)

            if "status" in df_3c.columns:
                fig = px.histogram(df_3c, x="status", title="Chamadas por status")
                st.plotly_chart(fig, use_container_width=True)
    except TresCPlusError as e:
        mostrar_erro_amigavel("Falha ao consultar 3C Plus", e)

# -------------------------------------------------------------
# EXACT
# -------------------------------------------------------------
with tab_exact:
    st.subheader("Exact Sales")
    try:
        cliente_exact = ExactClient()
        visao = st.selectbox(
            "Visão",
            [
                "Leads (todos)",
                "Leads vendidos",
                "Leads descartados",
                "Histórico de ligações",
                "Reuniões",
                "Dashboard - Desempenho de vendedores",
                "Dashboard - Desempenho de pré-vendedores",
                "Dashboard - Métricas de venda",
            ],
        )

        chave_exact = f"exact:{visao}:{data_inicio}:{data_fim}"

        def fetch_exact():
            di, df_ = data_inicio, data_fim
            if visao == "Leads (todos)":
                return cliente_exact.listar_leads(top=500).get("value", [])
            if visao == "Leads vendidos":
                return cliente_exact.listar_leads_vendidos(top=500).get("value", [])
            if visao == "Leads descartados":
                return cliente_exact.listar_leads_descartados(top=500).get("value", [])
            if visao == "Histórico de ligações":
                return cliente_exact.historico_ligacoes(top=500).get("value", [])
            if visao == "Reuniões":
                return cliente_exact.listar_reunioes(top=500).get("value", [])
            if visao == "Dashboard - Desempenho de vendedores":
                return cliente_exact.dashboard_desempenho_vendedores(di, df_)
            if visao == "Dashboard - Desempenho de pré-vendedores":
                return cliente_exact.dashboard_desempenho_pre_vendedores(di, df_)
            if visao == "Dashboard - Métricas de venda":
                return cliente_exact.dashboard_metricas_venda(di, df_)
            return []

        df_exact = carregar_com_cache(chave_exact, fetch_exact, ttl)
        if df_exact.empty:
            st.info("Nenhum registro retornado nessa visão/período.")
        else:
            st.metric("Total de registros", len(df_exact))
            st.dataframe(df_exact, use_container_width=True)

            # gráfico genérico se houver coluna categórica
            for col in ["status", "stage", "etapa", "situacao", "stageName", "funnelName"]:
                if col in df_exact.columns:
                    fig = px.histogram(df_exact, x=col, title=f"Distribuição por {col}")
                    st.plotly_chart(fig, use_container_width=True)
                    break
    except ExactError as e:
        mostrar_erro_amigavel("Falha ao consultar Exact", e)

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
