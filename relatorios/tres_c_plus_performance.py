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
            "- **Ligações:** todas as chamadas que a pessoa qualificou no período\n"
            "- **Atendidas:** quantas o cliente atendeu do outro lado (contato real)\n"
            "- **Viraram lead:** quantas ela marcou como *RD Station* — o sinal de "
            "que o cliente tem interesse e vai pro Exact\n"
            "- **Ligações por dia:** média só nos dias em que ela trabalhou"
        )

    try:
        cliente = TresCPlusClient()
    except TresCPlusError as e:
        st.error(f"Token 3C Plus não configurado: {e}")
        return

    chave = f"3cplus_qual_v2:{data_inicio}:{data_fim}"
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
    # ATENDIDAS (contato efetivo) — buscar antes dos cards
    # =========================================================
    chave_at = f"3cplus_atend_v1:{data_inicio}:{data_fim}"
    df_at = cache.buscar_df(chave_at, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df_at is None:
        start_str = f"{data_inicio.isoformat()} 00:00:00"
        end_str = f"{data_fim.isoformat()} 23:59:59"
        rows_at = []
        prog = st.progress(0, text="Buscando contato efetivo...")
        for i, (nome, agent_id) in enumerate(SDRS_3C_PLUS.items()):
            prog.progress((i + 1) / len(SDRS_3C_PLUS), text=f"Buscando {nome}...")
            try:
                dados_at = cliente.agent_statistics(start_str, end_str, agent_id=agent_id)
            except TresCPlusError:
                continue
            for dia in dados_at:
                rows_at.append({
                    "sdr": nome,
                    "dia": dia.get("date"),
                    "atendidas": dia.get("answered") or 0,
                    "convertidas": dia.get("converted") or 0,
                })
        prog.empty()
        df_at = pd.DataFrame(rows_at)
        cache.salvar_df(chave_at, df_at)

    # ---------- Números-base por pessoa ----------
    dias_periodo = (data_fim - data_inicio).days + 1
    BDRS = ["IASMIM", "CRISLANE", "JENNYFER"]
    CONSULTORAS = ["DANIELE", "LAIANE", "LAYLA"]
    RD = "RD STATION"

    def base(nome: str) -> dict:
        d = df[df["sdr"] == nome]
        total = int(d["quantidade"].sum())
        dias = int(d["dia"].nunique())
        rd = int(d.loc[d["resultado"] == RD, "quantidade"].sum())
        atend = 0
        if not df_at.empty:
            atend = int(df_at.loc[df_at["sdr"] == nome, "atendidas"].sum())
        return {
            "total": total,
            "dias": dias,
            "por_dia": (total / dias) if dias else 0.0,
            "atend": atend,
            "pct_atend": (atend / total * 100) if total else 0.0,
            "rd": rd,
            "pct_rd": (rd / total * 100) if total else 0.0,
            "pct_rd_atend": (rd / atend * 100) if atend else 0.0,
        }

    dados = {n: base(n) for n in BDRS + CONSULTORAS}

    # ---------- Resumo em uma frase ----------
    bdrs_com_dados = [n for n in BDRS if dados[n]["total"] > 0]
    if bdrs_com_dados:
        mais_volume = max(bdrs_com_dados, key=lambda n: dados[n]["por_dia"])
        mais_aprov = max(bdrs_com_dados, key=lambda n: dados[n]["pct_rd"])
        if mais_volume == mais_aprov:
            frase = (
                f"**{mais_volume}** lidera em volume "
                f"({dados[mais_volume]['por_dia']:.0f} ligações/dia) e em aproveitamento "
                f"({dados[mais_volume]['pct_rd']:.1f}% das ligações viram lead)."
            )
        else:
            frase = (
                f"**{mais_volume}** lidera em volume "
                f"({dados[mais_volume]['por_dia']:.0f} ligações/dia); "
                f"**{mais_aprov}** lidera em aproveitamento "
                f"({dados[mais_aprov]['pct_rd']:.1f}% das ligações viram lead)."
            )
        st.info(frase)

    # =========================================================
    # BDRs — funil de prospecção
    # =========================================================
    st.subheader("BDRs — prospecção no discador")
    st.caption(
        "Cada card é um funil: quantas ligações → quantas o cliente atendeu → "
        "quantas viraram lead (RD Station). O % é sobre o total de ligações."
    )

    cols = st.columns(3)
    for col, nome in zip(cols, BDRS):
        b = dados[nome]
        with col:
            st.markdown(f"#### {nome}")
            st.metric(
                "Ligações por dia", f"{b['por_dia']:.0f}",
                help=f"{b['total']} ligações em {b['dias']} dias ativos "
                     f"(de {dias_periodo} no período)",
            )
            st.metric(
                "Atendidas", f"{b['atend']:,}".replace(",", "."),
                delta=f"{b['pct_atend']:.0f}% das ligações",
                delta_color="off",
            )
            st.metric(
                "Viraram lead", b["rd"],
                delta=f"{b['pct_rd']:.1f}% das ligações",
                delta_color="off",
                help=f"{b['pct_rd_atend']:.0f}% das que foram atendidas",
            )

    st.divider()

    # =========================================================
    # Consultoras — atendimento
    # =========================================================
    st.subheader("Consultoras — atendimento a leads")
    st.caption(
        "Elas ligam para quem já é lead, então o volume é naturalmente menor. "
        "Não compare com as BDRs."
    )

    cols = st.columns(3)
    for col, nome in zip(cols, CONSULTORAS):
        b = dados[nome]
        with col:
            st.markdown(f"#### {nome}")
            a, c = st.columns(2)
            a.metric("Ligações", f"{b['total']:,}".replace(",", "."))
            c.metric("Dias ativos", f"{b['dias']}/{dias_periodo}")
            st.metric(
                "Atendidas", f"{b['atend']:,}".replace(",", "."),
                delta=f"{b['pct_atend']:.0f}%" if b["total"] else None,
                delta_color="off",
            )

    st.divider()

    # =========================================================
    # GRÁFICO — funil das BDRs lado a lado
    # =========================================================
    st.subheader("Funil das BDRs")

    rows_f = []
    for nome in BDRS:
        b = dados[nome]
        rows_f += [
            {"BDR": nome, "Etapa": "Ligações", "Qtd": b["total"]},
            {"BDR": nome, "Etapa": "Atendidas", "Qtd": b["atend"]},
            {"BDR": nome, "Etapa": "Viraram lead", "Qtd": b["rd"]},
        ]
    df_f = pd.DataFrame(rows_f)
    if df_f["Qtd"].sum() > 0:
        fig = px.bar(
            df_f, x="BDR", y="Qtd", color="Etapa", barmode="group", text="Qtd",
            title="Ligações → Atendidas → Leads, por BDR",
            color_discrete_map={
                "Ligações": "#CBD5E1", "Atendidas": "#378ADD", "Viraram lead": "#1D9E75",
            },
            log_y=True,
        )
        fig.update_layout(yaxis_title="Quantidade (escala log)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Escala logarítmica: as barras ficam comparáveis mesmo com 8.000 "
            "ligações e 50 leads no mesmo gráfico."
        )

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
