"""
Relatório de performance dos SDRs no Exact Spotter.

LÓGICA CORRETA (validada com dados reais em 25/05/2026):
- Um lead é "da SDR X" quando X foi quem transferiu o lead pro vendedor
  (a primeira transferência feita por X dentro do período).
- Isso resolve o problema de o campo `sdr` do lead mudar quando passa
  pro vendedor — perdemos rastreabilidade se filtrássemos só por `sdr`.

Fonte: GET /v3/transferHistory  (originUserId = quem transferiu)
       GET /v3/Leads?$filter=id in (...)  (stage atual + dados do lead)

Foco: IASMIM (436128), CRISLANE (442056), JENNYFER (448464)
Funil: 23120 (único)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.exact import ExactClient, ExactError
from db import cache

# =============================================================
# Configuração
# =============================================================
SDRS_FOCO = {
    436128: "IASMIM",
    442056: "CRISLANE",
    448464: "JENNYFER",
}

# Stages reais do funil 23120
STAGE_PROPOSTA = "PROPOSTA ENVIADA"
STAGE_GANHO = "NEGOCIO FECHADO"
STAGE_DESCARTE = "Descartado"
STAGES_INICIAIS = {"BDR", "SEM CONTATO", "TENTATIVA DE CONTATO"}

# =============================================================
# Coleta de dados
# =============================================================
def _coletar_transferencias(
    cliente: ExactClient,
    data_inicio: date,
    data_fim: date,
    max_paginas: int = 30,
    page_size: int = 500,
) -> list[dict]:
    """Coleta todas as transferências do período, ordenadas mais recente primeiro."""
    todos: list[dict] = []
    di_iso = data_inicio.isoformat()
    df_iso = data_fim.isoformat()
    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$orderby": "createdAt desc",
        }
        resp = cliente._get("/transferHistory", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        passou_periodo = False
        for t in itens:
            data_str = (t.get("createdAt") or "")[:10]
            if data_str < di_iso:
                passou_periodo = True
                break
            if data_str > df_iso:
                continue
            todos.append(t)
        if passou_periodo:
            break
        if len(itens) < page_size:
            break
    return todos


def _coletar_leads_por_ids(
    cliente: ExactClient,
    lead_ids: list[int],
    lote_size: int = 50,
) -> dict[int, dict]:
    """Busca leads completos por IDs em lotes usando operador OData 'in'."""
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


# =============================================================
# Processamento
# =============================================================
def _construir_dataframe(
    transferencias: list[dict],
    leads_completos: dict[int, dict],
    sdrs_foco: dict[int, str],
) -> pd.DataFrame:
    """
    Cada lead vira UMA linha. Coluna 'sdr_responsavel' é a SDR que fez a
    PRIMEIRA transferência desse lead dentro do período (ordenadas asc).

    `transferencias` vem desc, então invertemos pra pegar a mais antiga.
    """
    # Ordenar asc por data, e pegar a primeira transferência de cada lead
    # feita por uma das SDRs foco
    transf_asc = sorted(transferencias, key=lambda t: t.get("createdAt") or "")

    primeira_por_lead: dict[int, dict] = {}
    for t in transf_asc:
        lead_id = t.get("leadId")
        if not lead_id:
            continue
        if t.get("originUserId") not in sdrs_foco:
            continue
        if lead_id in primeira_por_lead:
            continue  # já temos a primeira (asc) - ignora outras
        primeira_por_lead[lead_id] = t

    rows = []
    for lead_id, t in primeira_por_lead.items():
        sdr_nome = sdrs_foco[t["originUserId"]]
        lead = leads_completos.get(lead_id)
        if not lead:
            # lead foi transferido mas não veio nos detalhes — pula
            continue
        sales = lead.get("salesRep") or {}
        source = lead.get("source") or {}
        rows.append({
            "lead_id": lead_id,
            "lead_nome": lead.get("lead"),
            "stage_atual": lead.get("stage"),
            "sdr_responsavel": sdr_nome,
            "vendedor_atual": sales.get("name") if sales.get("id") else None,
            "origem": source.get("value"),
            "data_cadastro": lead.get("registerDate"),
            "data_transferencia": t.get("createdAt"),
            "data_atualizacao": lead.get("updateDate"),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Conversão de tipos
    for col in ["data_cadastro", "data_transferencia", "data_atualizacao"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    df["dias_no_funil"] = (df["data_atualizacao"] - df["data_transferencia"]).dt.days
    return df


def _calcular_metricas(df_sdr: pd.DataFrame) -> dict[str, Any]:
    """Calcula métricas resumidas pra um SDR."""
    total = len(df_sdr)
    if total == 0:
        return {
            "leads": 0,
            "em_andamento": 0,
            "propostas": 0,
            "ganhos": 0,
            "descartes": 0,
            "conversao_pct": 0.0,
            "dias_medio_ganho": None,
        }
    em_andamento = df_sdr["stage_atual"].isin(STAGES_INICIAIS).sum()
    propostas = (df_sdr["stage_atual"] == STAGE_PROPOSTA).sum()
    ganhos = (df_sdr["stage_atual"] == STAGE_GANHO).sum()
    descartes = (df_sdr["stage_atual"] == STAGE_DESCARTE).sum()
    df_ganhos = df_sdr[df_sdr["stage_atual"] == STAGE_GANHO]
    dias = df_ganhos["dias_no_funil"].mean() if not df_ganhos.empty else None
    return {
        "leads": int(total),
        "em_andamento": int(em_andamento),
        "propostas": int(propostas),
        "ganhos": int(ganhos),
        "descartes": int(descartes),
        "conversao_pct": (ganhos / total * 100) if total else 0.0,
        "dias_medio_ganho": float(dias) if dias is not None and not pd.isna(dias) else None,
    }


# =============================================================
# Renderização
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    st.markdown("### 👥 Performance dos SDRs")
    st.caption(
        f"IASMIM · CRISLANE · JENNYFER | Funil principal | "
        f"{data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}"
    )
    with st.expander("ℹ️ Como o relatório conta os leads"):
        st.markdown(
            "**Lógica:** um lead é considerado 'da SDR' quando ela faz a primeira "
            "transferência dele pro vendedor dentro do período. Os números mostram "
            "o **status atual** desses leads (que podem já estar com vendedores).\n\n"
            "- **Em andamento:** leads ainda em fase inicial (BDR, Tentativa, Sem contato)\n"
            "- **Propostas:** o vendedor enviou proposta\n"
            "- **Ganhos:** virou venda fechada\n"
            "- **Descartes:** lead foi descartado em alguma etapa"
        )

    try:
        cliente = ExactClient()
    except ExactError as e:
        st.error(f"Token do Exact não configurado: {e}")
        return

    chave = f"sdr_v2:{data_inicio}:{data_fim}"
    df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df is None:
        with st.spinner("Coletando transferências do Exact..."):
            try:
                transferencias = _coletar_transferencias(cliente, data_inicio, data_fim)
            except ExactError as e:
                st.error(f"Erro ao buscar transferências: {e}")
                return

        # Filtrar pelos IDs das SDRs alvo
        transf_filtradas = [
            t for t in transferencias if t.get("originUserId") in SDRS_FOCO
        ]
        st.caption(f"Encontradas {len(transf_filtradas)} transferências das 3 SDRs no período.")

        if not transf_filtradas:
            st.info("Nenhuma transferência das 3 SDRs no período.")
            return

        lead_ids = list({t["leadId"] for t in transf_filtradas if t.get("leadId")})

        with st.spinner(f"Buscando detalhes de {len(lead_ids)} leads..."):
            try:
                leads_completos = _coletar_leads_por_ids(cliente, lead_ids)
            except ExactError as e:
                st.error(f"Erro ao buscar leads: {e}")
                return

        df = _construir_dataframe(transf_filtradas, leads_completos, SDRS_FOCO)
        cache.salvar_df(chave, df)
    else:
        # reconverter datas após vir do cache
        for col in ["data_cadastro", "data_transferencia", "data_atualizacao"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
        if "data_transferencia" in df.columns and "data_atualizacao" in df.columns:
            df["dias_no_funil"] = (df["data_atualizacao"] - df["data_transferencia"]).dt.days

    if df is None or df.empty:
        st.info("Nenhum lead processado.")
        return

    # =========================================================
    # CARDS — uma coluna por SDR
    # =========================================================
    cols = st.columns(3)
    for i, (sdr_id, nome) in enumerate(SDRS_FOCO.items()):
        df_sdr = df[df["sdr_responsavel"] == nome]
        m = _calcular_metricas(df_sdr)
        with cols[i]:
            st.markdown(f"#### {nome}")
            c1, c2 = st.columns(2)
            c1.metric("Total leads", m["leads"])
            c2.metric("Em andamento", m["em_andamento"])
            c3, c4 = st.columns(2)
            c3.metric("Propostas", m["propostas"])
            c4.metric("Ganhos ✅", m["ganhos"])
            c5, c6 = st.columns(2)
            c5.metric("Descartes ❌", m["descartes"])
            c6.metric("Conversão", f"{m['conversao_pct']:.1f}%")
            if m["dias_medio_ganho"] is not None:
                st.metric("Dias até ganho", f"{m['dias_medio_ganho']:.0f}d")
            else:
                st.metric("Dias até ganho", "—")

    st.divider()

    # =========================================================
    # GRÁFICO — comparativo
    # =========================================================
    dados = []
    for sdr_id, nome in SDRS_FOCO.items():
        df_sdr = df[df["sdr_responsavel"] == nome]
        m = _calcular_metricas(df_sdr)
        dados.extend([
            {"SDR": nome, "Status": "Em andamento", "Quantidade": m["em_andamento"]},
            {"SDR": nome, "Status": "Propostas", "Quantidade": m["propostas"]},
            {"SDR": nome, "Status": "Ganhos", "Quantidade": m["ganhos"]},
            {"SDR": nome, "Status": "Descartes", "Quantidade": m["descartes"]},
        ])
    df_g = pd.DataFrame(dados)
    fig = px.bar(
        df_g,
        x="SDR",
        y="Quantidade",
        color="Status",
        barmode="group",
        title="Comparativo de status entre SDRs",
        color_discrete_map={
            "Em andamento": "#878787",
            "Propostas": "#378ADD",
            "Ganhos": "#1D9E75",
            "Descartes": "#D85A30",
        },
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # =========================================================
    # TABELA DETALHADA
    # =========================================================
    st.subheader("Detalhamento dos leads")

    filtro_sdr = st.multiselect(
        "Filtrar por SDR",
        options=list(SDRS_FOCO.values()),
        default=list(SDRS_FOCO.values()),
    )
    filtro_stage = st.multiselect(
        "Filtrar por status",
        options=sorted(df["stage_atual"].dropna().unique()),
        default=sorted(df["stage_atual"].dropna().unique()),
    )

    df_filtrado = df[
        df["sdr_responsavel"].isin(filtro_sdr)
        & df["stage_atual"].isin(filtro_stage)
    ].copy()

    # Formatar pra exibição
    df_view = df_filtrado.copy()
    df_view["Transferência"] = df_view["data_transferencia"].dt.strftime("%d/%m/%Y")
    df_view = df_view[[
        "Transferência", "lead_nome", "stage_atual", "sdr_responsavel",
        "vendedor_atual", "origem", "dias_no_funil"
    ]].rename(columns={
        "lead_nome": "Lead",
        "stage_atual": "Status atual",
        "sdr_responsavel": "SDR (cadastrou)",
        "vendedor_atual": "Vendedor atual",
        "origem": "Origem",
        "dias_no_funil": "Dias",
    }).sort_values("Transferência", ascending=False)

    st.caption(f"{len(df_view)} leads filtrados")
    st.dataframe(df_view, use_container_width=True, hide_index=True)

    csv = df_view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Baixar CSV",
        csv,
        file_name=f"sdr_performance_{data_inicio}_{data_fim}.csv",
        mime="text/csv",
    )
