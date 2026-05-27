"""
Dashboard 3C Plus — produtividade dos operadores via /qualification/statistics.

Abordagem: usar o endpoint agregado /qualification/statistics?agent_id=X
em vez de paginar todas as chamadas (376 mil/mês). Super rápido (~5s).

Foco: IASMIM, CRISLANE, JENNYFER, DANIELE, LAIANE, LAYLA.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.tres_c_plus import TresCPlusClient, TresCPlusError
from db import cache


# IDs no 3C Plus (validado em 27/05/2026)
SDRS_3C_PLUS = {
    "IASMIM": 123722,
    "CRISLANE": 200133,
    "JENNYFER": 223044,
    "DANIELE": 102589,
    "LAIANE": 60516,
    "LAYLA": 183640,
}


# =============================================================
# Renderização
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    st.markdown("### 📞 3C Plus — Performance dos operadores")
    nomes_sdrs = list(SDRS_3C_PLUS.keys())
    st.caption(
        f"{' · '.join(nomes_sdrs)} | "
        f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    )

    with st.expander("ℹ️ Como o relatório conta as ligações"):
        st.markdown(
            "- **Chamadas qualificadas:** todas as ligações que a SDR qualificou no período\n"
            "- **Dias ativos:** quantos dias do período a SDR teve atividade\n"
            "- **Resultados:** o que a SDR marcou cada chamada como "
            "(Sem contato, Em negociação, Sem interesse, etc.)"
        )

    try:
        cliente = TresCPlusClient()
    except TresCPlusError as e:
        st.error(f"Token 3C Plus não configurado: {e}")
        return

    chave = f"3cplus_qual_v1:{data_inicio}:{data_fim}"
    df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df is None:
        start_str = f"{data_inicio.isoformat()} 00:00:00"
        end_str = f"{data_fim.isoformat()} 23:59:59"

        # Buscar /qualification/statistics POR AGENTE (rápido, agregado)
        rows = []
        progress = st.progress(0, text="Buscando estatísticas por SDR...")
        for i, (nome, agent_id) in enumerate(SDRS_3C_PLUS.items()):
            progress.progress((i + 1) / len(SDRS_3C_PLUS), text=f"Buscando {nome}...")
            try:
                dados = cliente.qualification_statistics(start_str, end_str, agent_id=agent_id)
            except TresCPlusError as e:
                st.warning(f"Não consegui buscar {nome}: {e}")
                continue

            for dia in dados:
                data_str = dia.get("date")
                for q_id, q_data in (dia.get("qualifications") or {}).items():
                    rows.append({
                        "sdr": nome,
                        "agent_id": agent_id,
                        "dia": data_str,
                        "resultado": q_data.get("name") or f"ID {q_id}",
                        "quantidade": q_data.get("count") or 0,
                    })
        progress.empty()

        df = pd.DataFrame(rows)
        cache.salvar_df(chave, df)

    if df.empty:
        st.info("Nenhuma chamada qualificada no período.")
        return

    # =========================================================
    # CARDS — 6 SDRs em 2 linhas × 3 colunas
    # =========================================================
    st.subheader("Produtividade por SDR")

    dias_periodo = (data_fim - data_inicio).days + 1

    def render_card_sdr(col, nome: str):
        df_sdr = df[df["sdr"] == nome]
        total_cham = int(df_sdr["quantidade"].sum())
        dias_ativos = df_sdr["dia"].nunique()
        cham_por_dia_ativo = total_cham / dias_ativos if dias_ativos else 0

        with col:
            st.markdown(f"#### {nome}")
            c1, c2 = st.columns(2)
            c1.metric("Chamadas", total_cham)
            c2.metric("Dias ativos", f"{dias_ativos}/{dias_periodo}")
            c3, c4 = st.columns(2)
            c3.metric("Cham/dia ativo", f"{cham_por_dia_ativo:.0f}")
            c4.metric("Cham/dia total", f"{(total_cham/dias_periodo):.0f}")

    # Linha 1: IASMIM, CRISLANE, JENNYFER
    cols1 = st.columns(3)
    for i, nome in enumerate(nomes_sdrs[:3]):
        render_card_sdr(cols1[i], nome)

    # Linha 2: DANIELE, LAIANE, LAYLA
    cols2 = st.columns(3)
    for i, nome in enumerate(nomes_sdrs[3:6]):
        render_card_sdr(cols2[i], nome)

    st.divider()

    # =========================================================
    # GRÁFICO — comparativo (total de chamadas por SDR)
    # =========================================================
    st.subheader("Comparativo entre SDRs")

    df_g = (
        df.groupby("sdr")["quantidade"].sum()
        .reindex(nomes_sdrs, fill_value=0)
        .reset_index()
        .rename(columns={"sdr": "SDR", "quantidade": "Chamadas"})
    )
    fig = px.bar(
        df_g, x="SDR", y="Chamadas",
        title="Total de chamadas qualificadas por SDR",
        text="Chamadas",
        color="SDR",
        color_discrete_map={
            "IASMIM": "#1D9E75",
            "CRISLANE": "#378ADD",
            "JENNYFER": "#E5A663",
            "DANIELE": "#9C27B0",
            "LAIANE": "#FF5722",
            "LAYLA": "#607D8B",
        },
    )
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # =========================================================
    # RESULTADOS DAS LIGAÇÕES — POR SDR INDIVIDUAL
    # =========================================================
    st.subheader("📋 Resultados das ligações por SDR")
    st.caption("Quantas vezes cada SDR teve cada resultado de chamada")

    # Tabela cruzada: Resultado x SDR
    pivot = (
        df.groupby(["resultado", "sdr"])["quantidade"].sum()
        .unstack(fill_value=0)
    )
    # Reordenar colunas seguindo SDRS_3C_PLUS
    col_order = [c for c in nomes_sdrs if c in pivot.columns]
    pivot = pivot[col_order]
    # Adicionar coluna TOTAL
    pivot["TOTAL"] = pivot.sum(axis=1)
    # Ordenar linhas pelo TOTAL decrescente
    pivot = pivot.sort_values("TOTAL", ascending=False)
    # Adicionar linha TOTAL no final
    total_row = pivot.sum(axis=0)
    total_row.name = "TOTAL"
    pivot = pd.concat([pivot, pd.DataFrame([total_row])])

    st.dataframe(pivot, use_container_width=True)

    # Gráfico empilhado horizontal
    df_long = (
        df.groupby(["resultado", "sdr"])["quantidade"].sum()
        .reset_index()
        .rename(columns={"resultado": "Resultado", "sdr": "SDR", "quantidade": "Quantidade"})
    )
    df_long = df_long[df_long["Quantidade"] > 0]

    if not df_long.empty:
        totais = df_long.groupby("Resultado")["Quantidade"].sum().sort_values(ascending=True)
        df_long["Resultado"] = pd.Categorical(
            df_long["Resultado"], categories=totais.index.tolist(), ordered=True
        )
        fig_q = px.bar(
            df_long.sort_values("Resultado"), x="Quantidade", y="Resultado", color="SDR",
            orientation="h", barmode="stack",
            title="Resultados das ligações — empilhado por SDR",
            color_discrete_map={
                "IASMIM": "#1D9E75",
                "CRISLANE": "#378ADD",
                "JENNYFER": "#E5A663",
                "DANIELE": "#9C27B0",
                "LAIANE": "#FF5722",
                "LAYLA": "#607D8B",
            },
            height=max(400, 30 * len(totais)),
        )
        st.plotly_chart(fig_q, use_container_width=True)

    st.divider()

    # =========================================================
    # EVOLUÇÃO DIÁRIA (linha do tempo)
    # =========================================================
    st.subheader("📈 Evolução diária por SDR")

    df_dia = (
        df.groupby(["dia", "sdr"])["quantidade"].sum()
        .reset_index()
        .rename(columns={"dia": "Dia", "sdr": "SDR", "quantidade": "Chamadas"})
    )
    df_dia["Dia"] = pd.to_datetime(df_dia["Dia"])
    df_dia = df_dia.sort_values("Dia")

    if not df_dia.empty:
        fig_dia = px.line(
            df_dia, x="Dia", y="Chamadas", color="SDR",
            title="Chamadas por dia",
            markers=True,
            color_discrete_map={
                "IASMIM": "#1D9E75",
                "CRISLANE": "#378ADD",
                "JENNYFER": "#E5A663",
                "DANIELE": "#9C27B0",
                "LAIANE": "#FF5722",
                "LAYLA": "#607D8B",
            },
        )
        st.plotly_chart(fig_dia, use_container_width=True)

    # Download CSV
    csv = pivot.to_csv().encode("utf-8-sig")
    st.download_button(
        "📥 Baixar tabela em CSV", csv,
        file_name=f"3cplus_resultados_{data_inicio}_{data_fim}.csv",
        mime="text/csv", key="dl_3c_resultados",
    )
