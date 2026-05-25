"""
Relatório de performance dos SDRs no Exact Spotter.

Foco: IASMIM, CRISLANE, JENNYFER
Métricas: leads cadastrados, propostas enviadas, ganhos, descartes,
taxa de conversão, tempo médio até ganho.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.exact import ExactClient, ExactError
from db import cache

# Configuração: quais SDRs aparecem no relatório
SDRS_FOCO = {
    436128: "IASMIM",
    442056: "CRISLANE",
    448464: "JENNYFER",
}

# Stages do funil 23120
STAGE_PROPOSTA = "PROPOSTA ENVIADA"
STAGE_GANHO = "NEGOCIO FECHADO"
STAGE_DESCARTE = "Descartado"
FUNIL_ID = 23120


def _coletar_leads_periodo(
    cliente: ExactClient,
    data_inicio: date,
    data_fim: date,
    max_paginas: int = 30,
    page_size: int = 500,
) -> list[dict]:
    """
    Pagina leads ordenados por data desc até esgotar o período.
    Para de paginar quando bate em leads anteriores ao data_inicio.
    """
    todos: list[dict] = []
    di_iso = data_inicio.isoformat()
    df_iso = data_fim.isoformat()

    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$orderby": "registerDate desc",
        }
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break

        # filtrar pelo período
        algum_no_periodo = False
        for lead in itens:
            data_str = lead.get("registerDate", "")
            if not data_str:
                continue
            data_lead = data_str[:10]  # YYYY-MM-DD
            if data_lead < di_iso:
                # passamos do período (resultados ordenados desc)
                # ainda pode ter outros nessa página, então não cortamos a coleta
                continue
            if data_lead > df_iso:
                continue
            algum_no_periodo = True
            todos.append(lead)

        # se a última data da página é anterior ao início, podemos parar
        ultima_data = (itens[-1].get("registerDate") or "")[:10]
        if ultima_data and ultima_data < di_iso:
            break

        if len(itens) < page_size:
            break

    return todos


def _processar_leads(leads: list[dict]) -> pd.DataFrame:
    """Converte lista de leads em DataFrame com colunas relevantes."""
    rows = []
    for l in leads:
        sdr = l.get("sdr") or {}
        sales = l.get("salesRep") or {}
        source = l.get("source") or {}
        rows.append({
            "id": l.get("id"),
            "lead": l.get("lead"),
            "stage": l.get("stage"),
            "sdr_id": sdr.get("id"),
            "sdr_nome": sdr.get("name"),
            "salesRep_nome": sales.get("name") if sales.get("id") else None,
            "origem": source.get("value"),
            "registerDate": l.get("registerDate"),
            "updateDate": l.get("updateDate"),
            "funnelId": l.get("funnelId"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["registerDate"] = pd.to_datetime(df["registerDate"], errors="coerce", utc=True)
    df["updateDate"] = pd.to_datetime(df["updateDate"], errors="coerce", utc=True)
    df["dias_no_funil"] = (df["updateDate"] - df["registerDate"]).dt.days
    return df


def _calcular_metricas_sdr(df_sdr: pd.DataFrame) -> dict[str, Any]:
    """Calcula KPIs de um SDR a partir do DataFrame filtrado."""
    total = len(df_sdr)
    if total == 0:
        return {
            "leads": 0,
            "propostas": 0,
            "ganhos": 0,
            "descartes": 0,
            "conversao_pct": 0.0,
            "dias_medio_ganho": None,
        }
    propostas = (df_sdr["stage"] == STAGE_PROPOSTA).sum()
    ganhos = (df_sdr["stage"] == STAGE_GANHO).sum()
    descartes = (df_sdr["stage"] == STAGE_DESCARTE).sum()
    df_ganhos = df_sdr[df_sdr["stage"] == STAGE_GANHO]
    dias_medio = (
        df_ganhos["dias_no_funil"].mean()
        if not df_ganhos.empty
        else None
    )
    return {
        "leads": int(total),
        "propostas": int(propostas),
        "ganhos": int(ganhos),
        "descartes": int(descartes),
        "conversao_pct": (ganhos / total * 100) if total else 0.0,
        "dias_medio_ganho": float(dias_medio) if dias_medio is not None and not pd.isna(dias_medio) else None,
    }


# =============================================================
# Renderização do relatório no Streamlit
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    """Renderiza o relatório dos SDRs na tela atual."""
    st.markdown("### 👥 Performance dos SDRs — IASMIM, CRISLANE, JENNYFER")
    st.caption(
        f"Funil principal · Período: {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    )

    chave_cache = f"sdr_perf:{data_inicio}:{data_fim}"

    try:
        cliente = ExactClient()
    except ExactError as e:
        st.error(f"Token do Exact não configurado: {e}")
        return

    # Buscar dados (com cache)
    df = None
    if usar_cache:
        df = cache.buscar_df(chave_cache, ttl_segundos=ttl_minutos * 60)

    if df is None:
        with st.spinner("Buscando leads no Exact..."):
            try:
                leads = _coletar_leads_periodo(cliente, data_inicio, data_fim)
                df = _processar_leads(leads)
                cache.salvar_df(chave_cache, df)
            except ExactError as e:
                st.error(f"Erro ao consultar Exact: {e}")
                return
    else:
        # cache retorna sem o dtype datetime; reconverter
        if not df.empty:
            df["registerDate"] = pd.to_datetime(df["registerDate"], errors="coerce", utc=True)
            df["updateDate"] = pd.to_datetime(df["updateDate"], errors="coerce", utc=True)
            df["dias_no_funil"] = (df["updateDate"] - df["registerDate"]).dt.days

    if df is None or df.empty:
        st.info("Nenhum lead encontrado no período.")
        return

    # Filtrar só os 3 SDRs
    df_focados = df[df["sdr_id"].isin(SDRS_FOCO.keys())].copy()

    if df_focados.empty:
        st.warning(
            f"Encontrei {len(df)} leads no período, mas nenhum cadastrado por "
            f"IASMIM, CRISLANE ou JENNYFER."
        )
        return

    # =========================================================
    # Cards: 3 colunas, uma por SDR
    # =========================================================
    cols = st.columns(3)
    for i, (sdr_id, nome) in enumerate(SDRS_FOCO.items()):
        df_sdr = df_focados[df_focados["sdr_id"] == sdr_id]
        m = _calcular_metricas_sdr(df_sdr)
        with cols[i]:
            st.markdown(f"#### {nome}")
            c1, c2 = st.columns(2)
            c1.metric("Leads", m["leads"])
            c2.metric("Propostas", m["propostas"])
            c3, c4 = st.columns(2)
            c3.metric("Ganhos ✅", m["ganhos"])
            c4.metric("Descartes ❌", m["descartes"])
            st.metric("Conversão", f"{m['conversao_pct']:.1f}%")
            if m["dias_medio_ganho"] is not None:
                st.metric("Dias até ganho (médio)", f"{m['dias_medio_ganho']:.1f}d")
            else:
                st.metric("Dias até ganho (médio)", "—")

    st.divider()

    # =========================================================
    # Gráfico comparativo
    # =========================================================
    dados_grafico = []
    for sdr_id, nome in SDRS_FOCO.items():
        df_sdr = df_focados[df_focados["sdr_id"] == sdr_id]
        m = _calcular_metricas_sdr(df_sdr)
        dados_grafico.extend([
            {"SDR": nome, "Status": "Propostas", "Quantidade": m["propostas"]},
            {"SDR": nome, "Status": "Ganhos", "Quantidade": m["ganhos"]},
            {"SDR": nome, "Status": "Descartes", "Quantidade": m["descartes"]},
        ])
    df_grafico = pd.DataFrame(dados_grafico)
    fig = px.bar(
        df_grafico,
        x="SDR",
        y="Quantidade",
        color="Status",
        barmode="group",
        title="Comparativo entre SDRs",
        color_discrete_map={
            "Propostas": "#378ADD",
            "Ganhos": "#1D9E75",
            "Descartes": "#D85A30",
        },
    )
    st.plotly_chart(fig, use_container_width=True)

    # =========================================================
    # Tabela detalhada
    # =========================================================
    st.subheader("Detalhamento dos leads")
    df_tabela = df_focados.copy()
    df_tabela["Data cadastro"] = df_tabela["registerDate"].dt.strftime("%d/%m/%Y")
    df_tabela_view = df_tabela[[
        "Data cadastro", "lead", "stage", "sdr_nome",
        "salesRep_nome", "origem", "dias_no_funil"
    ]].rename(columns={
        "lead": "Lead",
        "stage": "Status",
        "sdr_nome": "SDR",
        "salesRep_nome": "Vendedor",
        "origem": "Origem",
        "dias_no_funil": "Dias",
    }).sort_values("Data cadastro", ascending=False)

    st.dataframe(df_tabela_view, use_container_width=True, hide_index=True)

    # Download
    csv = df_tabela_view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Baixar CSV", csv, file_name=f"sdr_performance_{data_inicio}_{data_fim}.csv",
        mime="text/csv"
    )
