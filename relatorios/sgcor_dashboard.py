"""
Dashboard SGCor — 4 relatórios:
1. Produção - apólices emitidas no período + total em prêmio R$
2. Renovações - apólices vencendo nos próximos 30/60/90 dias
3. Inadimplência - parcelas não recebidas em atraso
4. Comissões - quanto vai entrar nos próximos 30 dias (repasses)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.sgcor import SGCorClient, SGCorError
from db import cache


def _fmt_brl(valor: float) -> str:
    """Formata número como R$."""
    if pd.isna(valor) or valor is None:
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _to_iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _flatten_proposta(p: dict) -> dict:
    """Achata os campos relevantes de uma proposta pra DataFrame."""
    return {
        "propostaId": p.get("propostaId"),
        "proposta": p.get("proposta"),
        "apolice": p.get("apolice"),
        "endosso": p.get("endosso"),
        "tipo": p.get("tipoLabel") or p.get("tipo"),
        "status": p.get("statusLabel") or p.get("status"),
        "nivel": p.get("nivelLabel") or p.get("nivel"),
        "segurado": (p.get("segurado") or {}).get("nome"),
        "cpf_cnpj": (p.get("segurado") or {}).get("cpf_cnpj"),
        "ramo": (p.get("ramo") or {}).get("nome"),
        "companhia": (p.get("companhia") or {}).get("nome"),
        "dataVigenciaInicial": p.get("dataVigenciaInicial"),
        "dataVigenciaFinal": p.get("dataVigenciaFinal"),
        "dataEmitida": p.get("dataEmitida"),
        "premioLiquido": float(p.get("premioLiquido") or 0),
        "premioTotal": float(p.get("premioTotal") or 0),
        "comissao": float(p.get("comissao") or 0),
    }


def _flatten_parcela(p: dict) -> dict:
    """Achata uma parcela não recebida ou repasse."""
    parcelas = p.get("parcelas") or []
    primeira = parcelas[0] if parcelas else {}
    return {
        "propostaId": p.get("propostaId"),
        "proposta": p.get("proposta"),
        "apolice": p.get("apolice"),
        "tipo": p.get("tipoDescricao") or p.get("tipo"),
        "status": p.get("statusDescricao") or p.get("status"),
        "segurado": p.get("segurado"),
        "cpfCnpj": p.get("cpfCnpjSegurado"),
        "ramo": p.get("ramo"),
        "companhia": p.get("companhia"),
        "premioLiquido": float(p.get("premioLiquido") or 0),
        "comissaoPercentual": float(p.get("comissaoPercentual") or 0),
        "dataVencimento": primeira.get("dataVencimento") or primeira.get("vencimento"),
        "valorParcela": float(primeira.get("valor") or primeira.get("valorParcela") or 0),
        "numeroParcela": primeira.get("numero") or primeira.get("numeroParcela"),
    }


# =============================================================
# Renderização
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    st.markdown("### 📑 SGCor — Dashboard")
    st.caption(
        f"Produção · Renovações · Inadimplência · Comissões | "
        f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    )

    try:
        cliente = SGCorClient()
    except SGCorError as e:
        st.error(f"SGCor não configurado: {e}")
        st.info("Configure SGCOR_EMAIL e SGCOR_SENHA nos secrets do Streamlit.")
        return

    # Sub-abas pra cada relatório
    aba_prod, aba_renov, aba_inadim, aba_comiss = st.tabs([
        "📦 Produção", "🔄 Renovações", "⚠️ Inadimplência", "💰 Comissões"
    ])

    # =========================================================
    # 1) PRODUÇÃO - apólices emitidas no período
    # =========================================================
    with aba_prod:
        st.markdown("#### Produção — apólices no período")
        st.caption("Apólices/propostas com início de vigência no período selecionado")

        chave = f"sgcor_producao:{data_inicio}:{data_fim}"
        df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

        if df is None:
            with st.spinner("Buscando produção..."):
                try:
                    lista = cliente.producao_pesquisar(
                        tipo_data="dataVigenciaInicial",
                        data_inicial=_to_iso(data_inicio),
                        data_final=_to_iso(data_fim),
                    )
                except SGCorError as e:
                    st.error(f"Erro: {e}")
                    return
                df = pd.DataFrame([_flatten_proposta(p) for p in lista])
                cache.salvar_df(chave, df)

        if df.empty:
            st.info("Nenhuma apólice no período.")
        else:
            # KPIs
            total = len(df)
            premio_total = df["premioTotal"].sum()
            comissao_total = df["comissao"].sum()
            ticket_medio = premio_total / total if total else 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Apólices", total)
            c2.metric("Prêmio total", _fmt_brl(premio_total))
            c3.metric("Comissão estimada", _fmt_brl(comissao_total))
            c4.metric("Ticket médio", _fmt_brl(ticket_medio))

            # Gráfico por ramo
            if df["ramo"].notna().any():
                por_ramo = df.groupby("ramo").agg(
                    qtd=("propostaId", "count"),
                    premio=("premioTotal", "sum"),
                ).reset_index().sort_values("premio", ascending=False).head(10)
                fig = px.bar(
                    por_ramo, x="premio", y="ramo", orientation="h",
                    title="Top 10 ramos por prêmio",
                    text=por_ramo["premio"].apply(_fmt_brl),
                )
                fig.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig, use_container_width=True)

            # Tabela detalhada
            st.subheader("Detalhamento")
            df_view = df[[
                "proposta", "apolice", "segurado", "ramo", "companhia",
                "dataVigenciaInicial", "dataVigenciaFinal",
                "premioLiquido", "premioTotal", "comissao", "status"
            ]].copy()
            df_view["premioLiquido"] = df_view["premioLiquido"].apply(_fmt_brl)
            df_view["premioTotal"] = df_view["premioTotal"].apply(_fmt_brl)
            df_view["comissao"] = df_view["comissao"].apply(_fmt_brl)
            st.dataframe(df_view, use_container_width=True, hide_index=True)

            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 Baixar CSV", csv,
                file_name=f"sgcor_producao_{data_inicio}_{data_fim}.csv",
                mime="text/csv", key="dl_producao")

    # =========================================================
    # 2) RENOVAÇÕES - apólices vencendo
    # =========================================================
    with aba_renov:
        st.markdown("#### Renovações — apólices vencendo")
        st.caption("Apólices com data de vigência FINAL nos próximos 30/60/90 dias")

        hoje = date.today()
        d30 = hoje + timedelta(days=30)
        d60 = hoje + timedelta(days=60)
        d90 = hoje + timedelta(days=90)

        chave_r = f"sgcor_renov:{hoje}"
        df_r = cache.buscar_df(chave_r, ttl_segundos=ttl_minutos * 60) if usar_cache else None

        if df_r is None:
            with st.spinner("Buscando vencimentos..."):
                try:
                    lista = cliente.producao_pesquisar(
                        tipo_data="dataVigenciaFinal",
                        data_inicial=_to_iso(hoje),
                        data_final=_to_iso(d90),
                    )
                except SGCorError as e:
                    st.error(f"Erro: {e}")
                    return
                df_r = pd.DataFrame([_flatten_proposta(p) for p in lista])
                cache.salvar_df(chave_r, df_r)

        if df_r.empty:
            st.info("Nenhuma apólice vencendo nos próximos 90 dias.")
        else:
            df_r["dataVigenciaFinal_dt"] = pd.to_datetime(df_r["dataVigenciaFinal"], errors="coerce")
            hoje_pd = pd.Timestamp(hoje)
            df_r["dias_para_vencer"] = (df_r["dataVigenciaFinal_dt"] - hoje_pd).dt.days

            # KPIs em 3 faixas
            ate30 = (df_r["dias_para_vencer"] <= 30).sum()
            ate60 = ((df_r["dias_para_vencer"] > 30) & (df_r["dias_para_vencer"] <= 60)).sum()
            ate90 = ((df_r["dias_para_vencer"] > 60) & (df_r["dias_para_vencer"] <= 90)).sum()

            c1, c2, c3 = st.columns(3)
            c1.metric("Vencem em ≤30 dias", ate30)
            c2.metric("Vencem em 31-60 dias", ate60)
            c3.metric("Vencem em 61-90 dias", ate90)

            # Tabela ordenada por urgência
            st.subheader("Apólices em ordem de vencimento")
            df_view = df_r[[
                "proposta", "apolice", "segurado", "ramo", "companhia",
                "dataVigenciaFinal", "dias_para_vencer", "premioTotal"
            ]].copy().sort_values("dias_para_vencer")
            df_view["premioTotal"] = df_view["premioTotal"].apply(_fmt_brl)
            st.dataframe(df_view, use_container_width=True, hide_index=True)

            csv = df_r.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 Baixar CSV", csv,
                file_name=f"sgcor_renovacoes_{hoje}.csv",
                mime="text/csv", key="dl_renov")

    # =========================================================
    # 3) INADIMPLÊNCIA - parcelas não recebidas
    # =========================================================
    with aba_inadim:
        st.markdown("#### Inadimplência — parcelas não recebidas")
        st.caption(f"Parcelas com vencimento entre {data_inicio.strftime('%d/%m/%Y')} e {data_fim.strftime('%d/%m/%Y')}")

        chave_i = f"sgcor_inadim:{data_inicio}:{data_fim}"
        df_i = cache.buscar_df(chave_i, ttl_segundos=ttl_minutos * 60) if usar_cache else None

        if df_i is None:
            with st.spinner("Buscando parcelas não recebidas..."):
                try:
                    lista = cliente.parcelas_nao_recebidas(
                        tipo_data="dataVencimento",
                        data_inicial=_to_iso(data_inicio),
                        data_final=_to_iso(data_fim),
                    )
                except SGCorError as e:
                    st.error(f"Erro: {e}")
                    return
                df_i = pd.DataFrame([_flatten_parcela(p) for p in lista])
                cache.salvar_df(chave_i, df_i)

        if df_i.empty:
            st.info("Nenhuma parcela não recebida no período.")
        else:
            total_qtd = len(df_i)
            total_valor = df_i["valorParcela"].sum()

            # Atraso: parcelas vencidas antes de hoje
            hoje_pd = pd.Timestamp(date.today())
            df_i["dataVencimento_dt"] = pd.to_datetime(df_i["dataVencimento"], errors="coerce")
            em_atraso = (df_i["dataVencimento_dt"] < hoje_pd).sum()
            valor_atraso = df_i[df_i["dataVencimento_dt"] < hoje_pd]["valorParcela"].sum()

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total parcelas", total_qtd)
            c2.metric("Valor total", _fmt_brl(total_valor))
            c3.metric("Em atraso", em_atraso)
            c4.metric("Valor em atraso", _fmt_brl(valor_atraso))

            st.subheader("Detalhamento")
            df_view = df_i[[
                "proposta", "apolice", "segurado", "ramo", "companhia",
                "dataVencimento", "valorParcela", "status"
            ]].copy().sort_values("dataVencimento")
            df_view["valorParcela"] = df_view["valorParcela"].apply(_fmt_brl)
            st.dataframe(df_view, use_container_width=True, hide_index=True)

            csv = df_i.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 Baixar CSV", csv,
                file_name=f"sgcor_inadimplencia_{data_inicio}_{data_fim}.csv",
                mime="text/csv", key="dl_inadim")

    # =========================================================
    # 4) COMISSÕES - repasses ao produtor (próximos 30 dias)
    # =========================================================
    with aba_comiss:
        st.markdown("#### Comissões — repasses a receber")
        hoje = date.today()
        d30 = hoje + timedelta(days=30)
        st.caption(f"Repasses ao produtor com vencimento entre {hoje.strftime('%d/%m/%Y')} e {d30.strftime('%d/%m/%Y')}")

        chave_c = f"sgcor_comiss:{hoje}"
        df_c = cache.buscar_df(chave_c, ttl_segundos=ttl_minutos * 60) if usar_cache else None

        if df_c is None:
            with st.spinner("Buscando repasses..."):
                try:
                    lista = cliente.parcelas_repasses(
                        tipo_data="dataVencimento",
                        data_inicial=_to_iso(hoje),
                        data_final=_to_iso(d30),
                    )
                except SGCorError as e:
                    st.error(f"Erro: {e}")
                    return
                df_c = pd.DataFrame([_flatten_parcela(p) for p in lista])
                cache.salvar_df(chave_c, df_c)

        if df_c.empty:
            st.info("Nenhum repasse previsto nos próximos 30 dias.")
        else:
            total_qtd = len(df_c)
            total_valor = df_c["valorParcela"].sum()

            c1, c2 = st.columns(2)
            c1.metric("Repasses previstos", total_qtd)
            c2.metric("Total a receber", _fmt_brl(total_valor))

            st.subheader("Repasses por data")
            df_view = df_c[[
                "proposta", "apolice", "segurado", "companhia",
                "dataVencimento", "valorParcela"
            ]].copy().sort_values("dataVencimento")
            df_view["valorParcela"] = df_view["valorParcela"].apply(_fmt_brl)
            st.dataframe(df_view, use_container_width=True, hide_index=True)

            csv = df_c.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 Baixar CSV", csv,
                file_name=f"sgcor_comissoes_{hoje}.csv",
                mime="text/csv", key="dl_comiss")
