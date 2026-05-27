"""
Dashboard 3C Plus — produtividade dos operadores e resultados das ligações.

Foco: SDRs ativas (IASMIM, CRISLANE, JENNYFER, DANIELE, LAIANE, LAYLA).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.tres_c_plus import TresCPlusClient, TresCPlusError
from db import cache


# Mapa nome amigável → id no 3C Plus (validado em 27/05/2026)
SDRS_3C_PLUS = {
    "IASMIM": 123722,
    "CRISLANE": 200133,
    "JENNYFER": 223044,
    "DANIELE": 102589,
    "LAIANE": 60516,
    "LAYLA": 183640,
}

# Mapeamento reverso: padrões no campo 'agent' (email) → nome amigável
# Como o campo `agent` da chamada vem como email/nome, fazemos match parcial
EMAIL_TO_NOME = {
    "iasmim": "IASMIM",
    "crislane": "CRISLANE",
    "jennyfer": "JENNYFER",
    "daniele": "DANIELE",
    "laiane": "LAIANE",
    "layla": "LAYLA",
}


def _normalizar_agent(agent_str: str | None) -> str | None:
    """Converte o campo 'agent' da chamada num nome amigável de SDR."""
    if not agent_str or agent_str == "-":
        return None
    s = agent_str.lower()
    for chave, nome in EMAIL_TO_NOME.items():
        if chave in s:
            return nome
    return None  # não é uma das nossas SDRs


def _hms_para_segundos(hms: str | None) -> int:
    """Converte 'HH:MM:SS' em segundos."""
    if not hms or hms == "-":
        return 0
    try:
        partes = hms.split(":")
        if len(partes) == 3:
            return int(partes[0]) * 3600 + int(partes[1]) * 60 + int(partes[2])
        if len(partes) == 2:
            return int(partes[0]) * 60 + int(partes[1])
    except (ValueError, AttributeError):
        pass
    return 0


def _segundos_para_hms(segundos: float) -> str:
    """Formata segundos em 'HH:MM:SS'."""
    if pd.isna(segundos) or segundos <= 0:
        return "00:00:00"
    s = int(segundos)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# =============================================================
# Renderização
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    st.markdown("### 📞 3C Plus — Performance dos operadores")
    st.caption(
        f"SDRs ativos | {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    )

    with st.expander("ℹ️ Como o relatório conta as ligações"):
        st.markdown(
            "- **Chamadas:** todas as ligações feitas pelo operador no período\n"
            "- **Tempo em ligação:** soma de `speaking_time` (tempo falando com o cliente)\n"
            "- **Tempo médio:** soma dividida pelo número de chamadas\n"
            "- **Resultados:** vem de `/qualification/statistics` — agrupado por dia"
        )

    try:
        cliente = TresCPlusClient()
    except TresCPlusError as e:
        st.error(f"Token 3C Plus não configurado: {e}")
        return

    chave = f"3cplus_v2:{data_inicio}:{data_fim}"
    df_chamadas = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df_chamadas is None:
        start_str = f"{data_inicio.isoformat()} 00:00:00"
        end_str = f"{data_fim.isoformat()} 23:59:59"

        with st.spinner(f"Buscando chamadas do 3C Plus..."):
            try:
                chamadas = cliente.listar_chamadas(start_str, end_str)
            except TresCPlusError as e:
                st.error(f"Erro ao buscar chamadas: {e}")
                return

        if not chamadas:
            st.info("Nenhuma chamada no período.")
            return

        # Converte pra DataFrame com colunas essenciais
        df_chamadas = pd.DataFrame([{
            "id": c.get("id"),
            "data_hora": c.get("call_date"),
            "agent": c.get("agent"),
            "campaign": c.get("campaign"),
            "number": c.get("number"),
            "speaking_seg": _hms_para_segundos(c.get("speaking_time")),
            "acw_seg": _hms_para_segundos(c.get("acw_time")),
            "list": c.get("list"),
        } for c in chamadas])

        # Normalizar agent → SDR nome
        df_chamadas["sdr"] = df_chamadas["agent"].apply(_normalizar_agent)
        df_chamadas["data_hora"] = pd.to_datetime(df_chamadas["data_hora"], errors="coerce")
        df_chamadas["dia"] = df_chamadas["data_hora"].dt.date

        cache.salvar_df(chave, df_chamadas)
    else:
        df_chamadas["data_hora"] = pd.to_datetime(df_chamadas["data_hora"], errors="coerce")
        df_chamadas["dia"] = df_chamadas["data_hora"].dt.date

    st.caption(f"📊 {len(df_chamadas)} chamadas totais no período (de todos os operadores)")

    # Filtrar só das SDRs alvo
    df_sdrs = df_chamadas[df_chamadas["sdr"].notna()].copy()
    st.caption(f"📊 {len(df_sdrs)} chamadas das SDRs alvo ({', '.join(SDRS_3C_PLUS.keys())})")

    if df_sdrs.empty:
        st.warning("Nenhuma chamada das SDRs alvo no período.")
        return

    # =========================================================
    # CARDS — uma coluna por SDR
    # =========================================================
    st.subheader("Produtividade por SDR")

    dias_periodo = (data_fim - data_inicio).days + 1

    # Calcular pra cada SDR
    cols = st.columns(3)
    sdrs_ativas = list(SDRS_3C_PLUS.keys())
    for i, nome in enumerate(sdrs_ativas[:3]):  # primeira linha: IASMIM, CRISLANE, JENNYFER
        df_sdr = df_sdrs[df_sdrs["sdr"] == nome]
        total_cham = len(df_sdr)
        cham_por_dia = total_cham / dias_periodo if dias_periodo else 0
        tempo_total = df_sdr["speaking_seg"].sum()
        tempo_medio = df_sdr["speaking_seg"].mean() if total_cham else 0

        with cols[i]:
            st.markdown(f"#### {nome}")
            c1, c2 = st.columns(2)
            c1.metric("Chamadas", total_cham)
            c2.metric("Cham/dia", f"{cham_por_dia:.1f}")
            c3, c4 = st.columns(2)
            c3.metric("Tempo total", _segundos_para_hms(tempo_total))
            c4.metric("Tempo médio", _segundos_para_hms(tempo_medio))

    # Segunda linha: DANIELE, LAIANE, LAYLA
    cols2 = st.columns(3)
    for i, nome in enumerate(sdrs_ativas[3:6]):
        df_sdr = df_sdrs[df_sdrs["sdr"] == nome]
        total_cham = len(df_sdr)
        cham_por_dia = total_cham / dias_periodo if dias_periodo else 0
        tempo_total = df_sdr["speaking_seg"].sum()
        tempo_medio = df_sdr["speaking_seg"].mean() if total_cham else 0

        with cols2[i]:
            st.markdown(f"#### {nome}")
            c1, c2 = st.columns(2)
            c1.metric("Chamadas", total_cham)
            c2.metric("Cham/dia", f"{cham_por_dia:.1f}")
            c3, c4 = st.columns(2)
            c3.metric("Tempo total", _segundos_para_hms(tempo_total))
            c4.metric("Tempo médio", _segundos_para_hms(tempo_medio))

    st.divider()

    # =========================================================
    # GRÁFICO — comparativo (chamadas + tempo)
    # =========================================================
    st.subheader("Comparativo entre SDRs")

    dados_g = []
    for nome in sdrs_ativas:
        df_sdr = df_sdrs[df_sdrs["sdr"] == nome]
        dados_g.append({
            "SDR": nome,
            "Chamadas": len(df_sdr),
            "Tempo total (min)": round(df_sdr["speaking_seg"].sum() / 60, 1),
        })
    df_g = pd.DataFrame(dados_g)
    if not df_g.empty:
        df_long = df_g.melt(id_vars="SDR", var_name="Métrica", value_name="Valor")
        fig = px.bar(
            df_long, x="SDR", y="Valor", color="Métrica", barmode="group",
            title="Chamadas e tempo total em ligação",
            color_discrete_map={
                "Chamadas": "#378ADD",
                "Tempo total (min)": "#1D9E75",
            },
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # =========================================================
    # GRÁFICO — resultados das ligações (qualifications)
    # =========================================================
    st.subheader("Resultados das ligações")

    chave_q = f"3cplus_qualif_v2:{data_inicio}:{data_fim}"
    df_q = cache.buscar_df(chave_q, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df_q is None:
        start_str = f"{data_inicio.isoformat()} 00:00:00"
        end_str = f"{data_fim.isoformat()} 23:59:59"
        with st.spinner("Buscando qualificações..."):
            try:
                qualif = cliente.qualification_statistics(start_str, end_str)
            except TresCPlusError as e:
                st.warning(f"Não consegui buscar qualificações: {e}")
                qualif = []

        # Estrutura vem como [{date, qualifications: {id: {name, count}, ...}}]
        rows_q = []
        for d in qualif:
            dia = d.get("date")
            for q_id, q_data in (d.get("qualifications") or {}).items():
                rows_q.append({
                    "dia": dia,
                    "resultado": q_data.get("name") or f"ID {q_id}",
                    "quantidade": q_data.get("count") or 0,
                })
        df_q = pd.DataFrame(rows_q)
        cache.salvar_df(chave_q, df_q)

    if df_q.empty:
        st.info("Sem dados de qualificação no período.")
    else:
        # Agrupar por resultado (somar dias)
        por_resultado = (
            df_q.groupby("resultado")["quantidade"].sum()
            .reset_index().sort_values("quantidade", ascending=True)
        )
        fig_q = px.bar(
            por_resultado.tail(15), x="quantidade", y="resultado",
            orientation="h",
            title="Top 15 resultados das ligações no período",
            text="quantidade",
        )
        st.plotly_chart(fig_q, use_container_width=True)

        # Tabela completa
        st.markdown("**Detalhamento por resultado**")
        st.dataframe(
            por_resultado.sort_values("quantidade", ascending=False),
            use_container_width=True, hide_index=True,
        )

    st.divider()

    # =========================================================
    # TABELA — chamadas detalhadas
    # =========================================================
    st.subheader("Chamadas detalhadas")

    filtro_sdr = st.multiselect(
        "Filtrar por SDR",
        options=sdrs_ativas,
        default=sdrs_ativas[:3],
    )

    df_t = df_sdrs[df_sdrs["sdr"].isin(filtro_sdr)].copy()

    df_t["Data/hora"] = df_t["data_hora"].dt.strftime("%d/%m/%Y %H:%M")
    df_t["Tempo fala"] = df_t["speaking_seg"].apply(_segundos_para_hms)
    df_view = df_t[[
        "Data/hora", "sdr", "number", "campaign", "Tempo fala",
    ]].rename(columns={
        "sdr": "SDR",
        "number": "Telefone",
        "campaign": "Campanha",
    }).sort_values("Data/hora", ascending=False)

    st.caption(f"{len(df_view)} chamadas filtradas")
    st.dataframe(df_view, use_container_width=True, hide_index=True)

    csv = df_view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Baixar CSV", csv,
        file_name=f"3cplus_chamadas_{data_inicio}_{data_fim}.csv",
        mime="text/csv", key="dl_3c_chamadas",
    )
