"""
Dashboard Exact - Performance das CONSULTORAS (vendedoras).

5 consultoras alvo: LAYLA, DANIELE, CARLA, CAMILA, LAIANE.

4 relatórios:
1. Propostas enviadas no período (via /leadStages)
2. Negócio fechado no período (via /leadStages com destinationStage='NEGOCIO FECHADO')
3. Em atraso no BDR (leads atualmente em BDR com updateDate > 1 dia)
4. Tempo médio até fechar (registerDate -> updateDate dos ganhos)

Consultor responsável de cada lead = campo `sdr` do /Leads
(que apesar do nome, vira a vendedora quando o lead é transferido).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.exact import ExactClient, ExactError
from db import cache


CONSULTORAS = {
    435660: "LAIANE",
    435661: "CARLA",
    435711: "CAMILA",
    435727: "DANIELE",
    435733: "LAYLA",
}

NOMES_CONSULTORAS = list(CONSULTORAS.values())

STAGE_PROPOSTA = "PROPOSTA ENVIADA"
STAGE_GANHO = "NEGOCIO FECHADO"
STAGE_BDR = "BDR"


# =============================================================
# Coleta de dados
# =============================================================
def _coletar_leadstages(
    cliente: ExactClient,
    destino: str,
    data_inicio: date,
    data_fim: date,
    max_paginas: int = 30,
    page_size: int = 500,
) -> list[dict]:
    """
    Coleta entradas do /leadStages onde destinationStage == destino,
    dentro do período (createdAt).
    """
    todos: list[dict] = []
    di_iso = data_inicio.isoformat()
    df_iso = data_fim.isoformat()
    filtro = f"destinationStage eq '{destino}'"

    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$filter": filtro,
            "$orderby": "createdAt desc",
        }
        resp = cliente._get("/leadStages", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        passou_periodo = False
        for s in itens:
            data_str = (s.get("createdAt") or "")[:10]
            if data_str < di_iso:
                passou_periodo = True
                break
            if data_str > df_iso:
                continue
            todos.append(s)
        if passou_periodo:
            break
        if len(itens) < page_size:
            break
    return todos


def _coletar_leads_por_ids(
    cliente: ExactClient,
    lead_ids: list[int],
    lote_size: int = 30,
) -> dict[int, dict]:
    """Busca leads completos por IDs em lotes via OData 'in'."""
    completos: dict[int, dict] = {}
    for i in range(0, len(lead_ids), lote_size):
        lote = lead_ids[i : i + lote_size]
        ids_str = ",".join(str(x) for x in lote)
        params = {"$filter": f"id in ({ids_str})", "$top": lote_size}
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        for l in itens or []:
            completos[l["id"]] = l
    return completos


def _coletar_leads_bdr_atual(
    cliente: ExactClient,
    max_paginas: int = 20,
    page_size: int = 500,
) -> list[dict]:
    """Lista TODOS os leads atualmente em BDR (status = 'BDR')."""
    todos: list[dict] = []
    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$filter": f"stage eq '{STAGE_BDR}'",
        }
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        todos.extend(itens)
        if len(itens) < page_size:
            break
    return todos


# =============================================================
# Renderização
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    st.markdown("### 🎯 Exact — Performance das consultoras")
    st.caption(
        f"{' · '.join(NOMES_CONSULTORAS)} | "
        f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    )

    with st.expander("ℹ️ Como o relatório conta"):
        st.markdown(
            "- **Propostas enviadas:** leads que ENTRARAM na etapa 'PROPOSTA ENVIADA' "
            "no período (via histórico de mudanças do funil)\n"
            "- **Negócio fechado:** leads que ENTRARAM em 'NEGOCIO FECHADO' no período\n"
            "- **Em atraso no BDR:** leads atualmente em BDR parados há mais de 1 dia "
            "(independente da data do filtro)\n"
            "- **Tempo médio:** dos negócios fechados no período, média de dias "
            "entre cadastro (registerDate) e fechamento (updateDate)"
        )

    try:
        cliente = ExactClient()
    except ExactError as e:
        st.error(f"Token Exact não configurado: {e}")
        return

    chave = f"exact_consultoras_v1:{data_inicio}:{data_fim}"
    dados = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if dados is None:
        # === 1) Propostas enviadas no período ===
        with st.spinner("Buscando propostas enviadas no período..."):
            try:
                propostas_stages = _coletar_leadstages(
                    cliente, STAGE_PROPOSTA, data_inicio, data_fim
                )
            except ExactError as e:
                st.error(f"Erro: {e}")
                return

        # === 2) Negócios fechados no período ===
        with st.spinner("Buscando negócios fechados no período..."):
            try:
                ganhos_stages = _coletar_leadstages(
                    cliente, STAGE_GANHO, data_inicio, data_fim
                )
            except ExactError as e:
                st.error(f"Erro: {e}")
                return

        # === 3) Leads atualmente em BDR ===
        with st.spinner("Buscando leads em BDR..."):
            try:
                leads_bdr = _coletar_leads_bdr_atual(cliente)
            except ExactError as e:
                st.error(f"Erro: {e}")
                return

        # Coletar IDs únicos pra buscar Leads completos
        ids_envolvidos = set()
        ids_envolvidos.update(p["leadId"] for p in propostas_stages)
        ids_envolvidos.update(g["leadId"] for g in ganhos_stages)

        with st.spinner(f"Buscando dados completos de {len(ids_envolvidos)} leads..."):
            try:
                leads_completos = _coletar_leads_por_ids(cliente, list(ids_envolvidos))
            except ExactError as e:
                st.error(f"Erro: {e}")
                return

        # === Montar DataFrames ===
        # Propostas: 1 linha por leadId+createdAt (no caso o lead pode ter entrado várias vezes)
        rows_prop = []
        for ls in propostas_stages:
            lead = leads_completos.get(ls["leadId"])
            if not lead:
                continue
            sdr_id = (lead.get("sdr") or {}).get("id")
            consultora = CONSULTORAS.get(sdr_id)
            if not consultora:
                continue  # ignora leads que não são das 5 consultoras
            rows_prop.append({
                "lead_id": ls["leadId"],
                "lead_nome": lead.get("lead"),
                "data_entrou": ls["createdAt"],
                "origem": ls.get("originStage"),
                "consultora": consultora,
                "status_atual": lead.get("stage"),
                "data_cadastro": lead.get("registerDate"),
                "data_update": lead.get("updateDate"),
            })

        rows_ganhos = []
        for ls in ganhos_stages:
            lead = leads_completos.get(ls["leadId"])
            if not lead:
                continue
            sdr_id = (lead.get("sdr") or {}).get("id")
            consultora = CONSULTORAS.get(sdr_id)
            if not consultora:
                continue
            # Tempo até fechar = data do ganho - data do cadastro
            try:
                cadastro = pd.to_datetime(lead.get("registerDate"), utc=True)
                ganho_dt = pd.to_datetime(ls["createdAt"], utc=True)
                dias = (ganho_dt - cadastro).days
            except (TypeError, ValueError):
                dias = None
            rows_ganhos.append({
                "lead_id": ls["leadId"],
                "lead_nome": lead.get("lead"),
                "data_ganho": ls["createdAt"],
                "consultora": consultora,
                "data_cadastro": lead.get("registerDate"),
                "dias_ate_fechar": dias,
            })

        # BDR em atraso: filtra leads atualmente em BDR cujo updateDate é mais antigo que 1 dia
        rows_bdr = []
        agora = datetime.now(timezone.utc)
        limite = agora - timedelta(days=1)
        for l in leads_bdr:
            sdr_id = (l.get("sdr") or {}).get("id")
            consultora = CONSULTORAS.get(sdr_id)
            if not consultora:
                continue
            try:
                upd = pd.to_datetime(l.get("updateDate"), utc=True)
            except (TypeError, ValueError):
                continue
            if upd < pd.Timestamp(limite):
                dias_parado = (agora - upd.to_pydatetime()).days
                rows_bdr.append({
                    "lead_id": l["id"],
                    "lead_nome": l.get("lead"),
                    "consultora": consultora,
                    "data_cadastro": l.get("registerDate"),
                    "data_update": l.get("updateDate"),
                    "dias_parado": dias_parado,
                })

        dados = {
            "propostas": pd.DataFrame(rows_prop),
            "ganhos": pd.DataFrame(rows_ganhos),
            "bdr_atraso": pd.DataFrame(rows_bdr),
        }
        cache.salvar_df(chave, dados)

    df_prop = dados.get("propostas", pd.DataFrame())
    df_ganhos = dados.get("ganhos", pd.DataFrame())
    df_bdr = dados.get("bdr_atraso", pd.DataFrame())

    # =========================================================
    # CARDS por consultora
    # =========================================================
    st.subheader("Resumo por consultora")

    def conta_por(df, coluna_consultora="consultora"):
        if df.empty:
            return {n: 0 for n in NOMES_CONSULTORAS}
        cont = df[coluna_consultora].value_counts().to_dict()
        return {n: int(cont.get(n, 0)) for n in NOMES_CONSULTORAS}

    propostas_por = conta_por(df_prop)
    ganhos_por = conta_por(df_ganhos)
    bdr_por = conta_por(df_bdr)

    # Tempo médio por consultora (ganhos com dias_ate_fechar válido)
    tempo_medio_por = {}
    if not df_ganhos.empty:
        for n in NOMES_CONSULTORAS:
            df_n = df_ganhos[(df_ganhos["consultora"] == n) & df_ganhos["dias_ate_fechar"].notna()]
            tempo_medio_por[n] = df_n["dias_ate_fechar"].mean() if not df_n.empty else None

    cols = st.columns(5)
    for i, nome in enumerate(NOMES_CONSULTORAS):
        with cols[i]:
            st.markdown(f"#### {nome}")
            st.metric("Propostas", propostas_por.get(nome, 0))
            st.metric("Ganhos ✅", ganhos_por.get(nome, 0))
            st.metric("Em atraso BDR ⚠️", bdr_por.get(nome, 0))
            t = tempo_medio_por.get(nome)
            st.metric("Dias até fechar (média)", f"{t:.0f}" if t is not None else "—")

    st.divider()

    # =========================================================
    # GRÁFICO comparativo
    # =========================================================
    st.subheader("Comparativo entre consultoras")

    dados_g = []
    for nome in NOMES_CONSULTORAS:
        dados_g.append({"Consultora": nome, "Métrica": "Propostas enviadas", "Quantidade": propostas_por.get(nome, 0)})
        dados_g.append({"Consultora": nome, "Métrica": "Ganhos", "Quantidade": ganhos_por.get(nome, 0)})
        dados_g.append({"Consultora": nome, "Métrica": "Em atraso BDR", "Quantidade": bdr_por.get(nome, 0)})
    df_g = pd.DataFrame(dados_g)
    fig = px.bar(
        df_g, x="Consultora", y="Quantidade", color="Métrica", barmode="group",
        title="Propostas · Ganhos · Em atraso BDR",
        color_discrete_map={
            "Propostas enviadas": "#378ADD",
            "Ganhos": "#1D9E75",
            "Em atraso BDR": "#D85A30",
        },
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # =========================================================
    # ABAS DETALHADAS
    # =========================================================
    aba_prop, aba_ganho, aba_bdr, aba_tempo = st.tabs([
        "📤 Propostas enviadas",
        "✅ Negócios fechados",
        "⚠️ Em atraso BDR",
        "⏱️ Tempo até fechar",
    ])

    def baixar_csv(df, nome):
        if not df.empty:
            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                f"📥 Baixar {nome} em CSV", csv,
                file_name=f"exact_{nome}_{data_inicio}_{data_fim}.csv",
                mime="text/csv", key=f"dl_{nome}",
            )

    with aba_prop:
        st.markdown(f"**{len(df_prop)} propostas enviadas no período**")
        if df_prop.empty:
            st.info("Nenhuma proposta no período.")
        else:
            df_v = df_prop.copy()
            df_v["Data entrou em proposta"] = pd.to_datetime(df_v["data_entrou"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
            df_v = df_v[["Data entrou em proposta", "consultora", "lead_nome", "origem", "status_atual"]].rename(columns={
                "consultora": "Consultora",
                "lead_nome": "Lead",
                "origem": "Origem (etapa anterior)",
                "status_atual": "Status atual",
            }).sort_values("Data entrou em proposta", ascending=False)
            st.dataframe(df_v, use_container_width=True, hide_index=True)
            baixar_csv(df_prop, "propostas")

    with aba_ganho:
        st.markdown(f"**{len(df_ganhos)} negócios fechados no período**")
        if df_ganhos.empty:
            st.info("Nenhum ganho no período.")
        else:
            df_v = df_ganhos.copy()
            df_v["Data ganho"] = pd.to_datetime(df_v["data_ganho"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
            df_v["Cadastrado"] = pd.to_datetime(df_v["data_cadastro"], errors="coerce").dt.strftime("%d/%m/%Y")
            df_v = df_v[["Data ganho", "consultora", "lead_nome", "Cadastrado", "dias_ate_fechar"]].rename(columns={
                "consultora": "Consultora",
                "lead_nome": "Lead",
                "dias_ate_fechar": "Dias até fechar",
            }).sort_values("Data ganho", ascending=False)
            st.dataframe(df_v, use_container_width=True, hide_index=True)
            baixar_csv(df_ganhos, "ganhos")

    with aba_bdr:
        st.markdown(f"**{len(df_bdr)} leads em BDR parados há mais de 1 dia**")
        if df_bdr.empty:
            st.info("Nenhum lead em atraso no BDR.")
        else:
            df_v = df_bdr.copy()
            df_v["Cadastrado"] = pd.to_datetime(df_v["data_cadastro"], errors="coerce").dt.strftime("%d/%m/%Y")
            df_v["Última atualização"] = pd.to_datetime(df_v["data_update"], errors="coerce").dt.strftime("%d/%m/%Y")
            df_v = df_v[["consultora", "lead_nome", "Cadastrado", "Última atualização", "dias_parado"]].rename(columns={
                "consultora": "Consultora",
                "lead_nome": "Lead",
                "dias_parado": "Dias parado",
            }).sort_values("Dias parado", ascending=False)
            st.dataframe(df_v, use_container_width=True, hide_index=True)
            baixar_csv(df_bdr, "bdr_atraso")

    with aba_tempo:
        st.markdown("**Tempo médio entre cadastro e fechamento por consultora**")
        if df_ganhos.empty:
            st.info("Nenhum ganho no período pra calcular tempo médio.")
        else:
            tabela_tempo = []
            for nome in NOMES_CONSULTORAS:
                df_n = df_ganhos[(df_ganhos["consultora"] == nome) & df_ganhos["dias_ate_fechar"].notna()]
                if df_n.empty:
                    tabela_tempo.append({"Consultora": nome, "Ganhos": 0, "Dias mínimo": "—", "Dias médio": "—", "Dias máximo": "—"})
                else:
                    tabela_tempo.append({
                        "Consultora": nome,
                        "Ganhos": len(df_n),
                        "Dias mínimo": int(df_n["dias_ate_fechar"].min()),
                        "Dias médio": f"{df_n['dias_ate_fechar'].mean():.1f}",
                        "Dias máximo": int(df_n["dias_ate_fechar"].max()),
                    })
            st.dataframe(pd.DataFrame(tabela_tempo), use_container_width=True, hide_index=True)

            # Gráfico boxplot de dias até fechar
            df_box = df_ganhos[df_ganhos["dias_ate_fechar"].notna()].copy()
            if not df_box.empty:
                fig_box = px.box(
                    df_box, x="consultora", y="dias_ate_fechar",
                    title="Distribuição do tempo até fechar (em dias)",
                    color="consultora",
                    color_discrete_map={
                        "LAIANE": "#FF5722",
                        "CARLA": "#9C27B0",
                        "CAMILA": "#607D8B",
                        "DANIELE": "#E5A663",
                        "LAYLA": "#378ADD",
                    },
                )
                fig_box.update_layout(showlegend=False)
                st.plotly_chart(fig_box, use_container_width=True)
