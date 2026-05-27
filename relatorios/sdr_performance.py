"""
Relatório de performance dos SDRs no Exact Spotter.

CRITÉRIOS (definidos com usuário em 25/05/2026):
- Total de leads = leads CADASTRADOS no período (registerDate)
- Em andamento = leads cadastrados no período que NÃO são ganho nem descarte
- Propostas = leads que AGORA estão com status 'PROPOSTA ENVIADA' (sem filtro de data)
- Ganhos = leads que mudaram pra 'NEGOCIO FECHADO' no período (updateDate),
           INDEPENDENTE de quando foram cadastrados
- Descartes = leads que mudaram pra 'Descartado' no período (updateDate),
              INDEPENDENTE de quando foram cadastrados

SDR responsável de cada lead: quem fez a primeira transferência (originUserId em transferHistory)
Foco: IASMIM (436128), CRISLANE (442056), JENNYFER (448464) | Funil 23120
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.exact import ExactClient, ExactError
from db import cache

SDRS_FOCO = {
    436128: "IASMIM",
    442056: "CRISLANE",
    448464: "JENNYFER",
}

STAGE_PROPOSTA = "PROPOSTA ENVIADA"
STAGE_GANHO = "NEGOCIO FECHADO"
STAGE_DESCARTE = "Descartado"


# =============================================================
# Coleta de dados
# =============================================================
def _coletar_leads_filtrados(
    cliente: ExactClient,
    data_inicio: date,
    data_fim: date,
    campo_data: str = "registerDate",
    filtro_extra: str | None = None,
    max_paginas: int = 30,
    page_size: int = 500,
) -> list[dict]:
    """
    Coleta leads filtrando por campo de data (registerDate ou updateDate).
    Pagina ordenando desc até passar do período.
    """
    todos: list[dict] = []
    di_iso = data_inicio.isoformat()
    df_iso = data_fim.isoformat()

    params_base = {"$orderby": f"{campo_data} desc"}
    if filtro_extra:
        params_base["$filter"] = filtro_extra

    for i in range(max_paginas):
        params = {**params_base, "$top": page_size, "$skip": i * page_size}
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        passou_periodo = False
        for l in itens:
            data_str = (l.get(campo_data) or "")[:10]
            if data_str < di_iso:
                passou_periodo = True
                break
            if data_str > df_iso:
                continue
            todos.append(l)
        if passou_periodo:
            break
        if len(itens) < page_size:
            break
    return todos


def _coletar_leads_atuais_propostas(
    cliente: ExactClient,
    max_paginas: int = 20,
    page_size: int = 500,
) -> list[dict]:
    """Coleta TODOS os leads atualmente com status PROPOSTA ENVIADA (sem filtro de data)."""
    todos: list[dict] = []
    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$filter": f"stage eq '{STAGE_PROPOSTA}'",
        }
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        todos.extend(itens)
        if len(itens) < page_size:
            break
    return todos


def _coletar_transferencias_dos_leads(
    cliente: ExactClient,
    lead_ids: set[int],
    max_paginas: int = 50,
    page_size: int = 500,
) -> dict[int, int]:
    """
    Pagina transferHistory inteiro e retorna mapa {lead_id: sdr_id da primeira SDR foco}.
    Itera asc pra pegar a PRIMEIRA transferência feita por uma SDR foco.
    """
    primeira_sdr: dict[int, int] = {}
    sdr_ids = set(SDRS_FOCO.keys())

    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$orderby": "createdAt asc",
        }
        resp = cliente._get("/transferHistory", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        for t in itens:
            lid = t.get("leadId")
            if lid not in lead_ids:
                continue
            origin = t.get("originUserId")
            if origin in sdr_ids and lid not in primeira_sdr:
                primeira_sdr[lid] = origin
        if len(itens) < page_size:
            break
        if len(primeira_sdr) >= len(lead_ids):
            break
    return primeira_sdr


def _coletar_descartes_por_lead(
    cliente: ExactClient,
    lead_ids: set[int],
    max_paginas: int = 30,
    page_size: int = 500,
) -> dict[int, dict]:
    """Mapa {lead_id: {stage, reason, date}} pros leads passados."""
    descartes: dict[int, dict] = {}
    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$orderby": "date desc",
        }
        resp = cliente._get("/Losts", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        for d in itens:
            lid = d.get("leadId")
            if lid in lead_ids and lid not in descartes:
                descartes[lid] = {
                    "stage": d.get("stage"),
                    "reason": d.get("reason"),
                    "date": d.get("date"),
                }
        if len(itens) < page_size:
            break
        if len(descartes) >= len(lead_ids):
            break
    return descartes


# =============================================================
# Processamento
# =============================================================
def _montar_linha(
    lead: dict,
    sdr_nome: str,
    descarte: dict | None = None,
    categoria: str = "",
) -> dict:
    """Monta uma linha do DataFrame a partir de um lead."""
    sales = lead.get("salesRep") or {}
    source = lead.get("source") or {}
    descarte = descarte or {}
    return {
        "lead_id": lead.get("id"),
        "lead_nome": lead.get("lead"),
        "stage_atual": lead.get("stage"),
        "sdr_responsavel": sdr_nome,
        "vendedor_atual": sales.get("name") if sales.get("id") else None,
        "origem": source.get("value"),
        "data_cadastro": lead.get("registerDate"),
        "data_atualizacao": lead.get("updateDate"),
        "categoria": categoria,
        "stage_descarte": descarte.get("stage"),
        "motivo_descarte": descarte.get("reason"),
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
            "- **Total de leads:** cadastrados no Spotter dentro do período\n"
            "- **Em andamento:** leads cadastrados no período que NÃO são ganho nem descarte\n"
            "- **Propostas enviadas:** leads cadastrados no período que estão "
            "atualmente com status 'PROPOSTA ENVIADA'\n"
            "- **Ganhos:** leads que fecharam venda no período, "
            "**independente da data de cadastro** — pode ser lead de meses anteriores\n"
            "- **Descartados:** leads cadastrados no período que estão "
            "atualmente com status 'Descartado'"
        )

    try:
        cliente = ExactClient()
    except ExactError as e:
        st.error(f"Token do Exact não configurado: {e}")
        return

    chave = f"sdr_v7:{data_inicio}:{data_fim}"
    df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df is None:
        # === 1) Leads cadastrados no período ===
        with st.spinner(f"Buscando leads cadastrados de {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}..."):
            try:
                leads_cadastrados = _coletar_leads_filtrados(
                    cliente, data_inicio, data_fim, campo_data="registerDate"
                )
            except ExactError as e:
                st.error(f"Erro ao buscar leads cadastrados: {e}")
                return

        # === 2) Leads que viraram NEGOCIO FECHADO no período ===
        # ÚNICA exceção: ganhos contam pelo updateDate, independente do registerDate
        with st.spinner("Buscando ganhos do período (independente da data de cadastro)..."):
            try:
                leads_ganhos = _coletar_leads_filtrados(
                    cliente, data_inicio, data_fim,
                    campo_data="updateDate",
                    filtro_extra=f"stage eq '{STAGE_GANHO}'",
                )
            except ExactError as e:
                st.error(f"Erro ao buscar ganhos: {e}")
                return

        # === 5) Identificar SDR de cada lead via transferHistory ===
        todos_ids = set()
        todos_ids.update(l["id"] for l in leads_cadastrados)
        todos_ids.update(l["id"] for l in leads_ganhos)

        if not todos_ids:
            st.info("Nenhum lead no período.")
            return

        with st.spinner(f"Identificando SDR de {len(todos_ids)} leads..."):
            try:
                primeira_sdr_por_lead = _coletar_transferencias_dos_leads(
                    cliente, todos_ids
                )
            except ExactError as e:
                st.error(f"Erro ao buscar transferências: {e}")
                return

        # === 6) Descartes (motivo + etapa) só pros leads cadastrados que estão descartados
        ids_cadastrados_descartados = {
            l["id"] for l in leads_cadastrados if l.get("stage") == STAGE_DESCARTE
        }
        with st.spinner("Buscando motivos de descarte..."):
            try:
                descartes_info = _coletar_descartes_por_lead(cliente, ids_cadastrados_descartados)
            except ExactError as e:
                st.warning(f"Não consegui buscar motivos: {e}")
                descartes_info = {}

        # === 7) Montar DataFrame ===
        # Após nova definição (v6), o DataFrame tem:
        # - 'cadastrado' (leads cadastrados no período) → base pra Total, Em andamento,
        #   Propostas enviadas, Descartados (filtrados por stage_atual)
        # - 'ganho' (leads ganhos no período via updateDate, INDEPENDENTE de cadastro)
        # Um mesmo lead pode aparecer em AMBAS categorias (cadastrado e ganho no mesmo mês).
        # Isso é intencional: ganhos sempre conta tudo que fechou no período.
        rows = []
        ids_cadastrados = set()
        for lead in leads_cadastrados:
            sdr_id = primeira_sdr_por_lead.get(lead["id"])
            if sdr_id not in SDRS_FOCO:
                continue
            descarte = descartes_info.get(lead["id"])
            rows.append(_montar_linha(lead, SDRS_FOCO[sdr_id], descarte=descarte, categoria="cadastrado"))
            ids_cadastrados.add(lead["id"])

        for lead in leads_ganhos:
            sdr_id = primeira_sdr_por_lead.get(lead["id"])
            if sdr_id not in SDRS_FOCO:
                continue
            rows.append(_montar_linha(lead, SDRS_FOCO[sdr_id], categoria="ganho"))

        df = pd.DataFrame(rows)
        if df.empty:
            st.info("Nenhum lead das 3 SDRs encontrado.")
            return

        for col in ["data_cadastro", "data_atualizacao"]:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

        cache.salvar_df(chave, df)
    else:
        for col in ["data_cadastro", "data_atualizacao"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    if df is None or df.empty:
        st.info("Nenhum lead processado.")
        return

    # =========================================================
    # CARDS — uma coluna por SDR
    # =========================================================
    cols = st.columns(3)
    for i, (sdr_id, nome) in enumerate(SDRS_FOCO.items()):
        df_sdr = df[df["sdr_responsavel"] == nome]

        # Total de leads = cadastrados no período (deduplica)
        df_cadastrados = df_sdr[df_sdr["categoria"] == "cadastrado"].drop_duplicates("lead_id")
        total_leads = len(df_cadastrados)

        # Em andamento = cadastrados que NÃO são ganho nem descarte (status atual)
        em_andamento = (~df_cadastrados["stage_atual"].isin(
            [STAGE_GANHO, STAGE_DESCARTE]
        )).sum()

        # Propostas enviadas = cadastrados no período com status atual = PROPOSTA ENVIADA
        propostas = (df_cadastrados["stage_atual"] == STAGE_PROPOSTA).sum()

        # Ganhos = categoria 'ganho' (independente da data de cadastro)
        ganhos = len(df_sdr[df_sdr["categoria"] == "ganho"].drop_duplicates("lead_id"))

        # Descartados = cadastrados no período com status atual = Descartado
        descartes = (df_cadastrados["stage_atual"] == STAGE_DESCARTE).sum()

        # Conversão: ganhos / total cadastrados no período
        conversao = (ganhos / total_leads * 100) if total_leads > 0 else 0.0

        with cols[i]:
            st.markdown(f"#### {nome}")
            c1, c2 = st.columns(2)
            c1.metric("Total leads", total_leads)
            c2.metric("Em andamento", int(em_andamento))
            c3, c4 = st.columns(2)
            c3.metric("Propostas enviadas", propostas)
            c4.metric("Ganhos ✅", ganhos)
            c5, c6 = st.columns(2)
            c5.metric("Descartados ❌", descartes)
            c6.metric("Conversão", f"{conversao:.1f}%")

    st.divider()

    # =========================================================
    # GRÁFICO — comparativo
    # =========================================================
    dados = []
    for sdr_id, nome in SDRS_FOCO.items():
        df_sdr = df[df["sdr_responsavel"] == nome]
        df_cadastrados = df_sdr[df_sdr["categoria"] == "cadastrado"].drop_duplicates("lead_id")
        em_andamento = (~df_cadastrados["stage_atual"].isin(
            [STAGE_GANHO, STAGE_DESCARTE]
        )).sum()
        propostas = (df_cadastrados["stage_atual"] == STAGE_PROPOSTA).sum()
        ganhos = len(df_sdr[df_sdr["categoria"] == "ganho"].drop_duplicates("lead_id"))
        descartes = (df_cadastrados["stage_atual"] == STAGE_DESCARTE).sum()

        dados.extend([
            {"SDR": nome, "Status": "Em andamento", "Quantidade": int(em_andamento)},
            {"SDR": nome, "Status": "Propostas enviadas", "Quantidade": int(propostas)},
            {"SDR": nome, "Status": "Ganhos", "Quantidade": ganhos},
            {"SDR": nome, "Status": "Descartados", "Quantidade": int(descartes)},
        ])
    df_g = pd.DataFrame(dados)
    fig = px.bar(
        df_g, x="SDR", y="Quantidade", color="Status", barmode="group",
        title="Comparativo entre SDRs",
        color_discrete_map={
            "Em andamento": "#878787",
            "Propostas enviadas": "#378ADD",
            "Ganhos": "#1D9E75",
            "Descartados": "#D85A30",
        },
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # =========================================================
    # GRÁFICO — etapa de descarte (só leads cadastrados no período)
    # =========================================================
    df_desc = df[
        (df["categoria"] == "cadastrado") &
        (df["stage_atual"] == STAGE_DESCARTE) &
        df["stage_descarte"].notna()
    ].drop_duplicates("lead_id")
    if not df_desc.empty:
        st.subheader("📉 Em qual etapa os leads foram descartados")
        st.caption(
            "Descartes apenas de leads cadastrados no período. "
            "Descartes em 'BDR' / 'TENTATIVA' = lead pouco qualificado. "
            "Descartes em 'PROPOSTA ENVIADA' = perda na negociação."
        )
        grouped = (
            df_desc.groupby(["sdr_responsavel", "stage_descarte"]).size()
            .reset_index(name="Quantidade")
            .rename(columns={"sdr_responsavel": "SDR", "stage_descarte": "Etapa"})
        )
        fig_d = px.bar(
            grouped, x="SDR", y="Quantidade", color="Etapa", barmode="group",
            title="Descartes por etapa do funil",
            category_orders={"Etapa": ["BDR", "SEM CONTATO", "TENTATIVA DE CONTATO", "PROPOSTA ENVIADA"]},
            color_discrete_map={
                "BDR": "#C8C8C8",
                "SEM CONTATO": "#9F9F9F",
                "TENTATIVA DE CONTATO": "#E5A663",
                "PROPOSTA ENVIADA": "#D85A30",
            },
        )
        st.plotly_chart(fig_d, use_container_width=True)

        st.markdown("**Top motivos de descarte**")
        motivos = (
            df_desc["motivo_descarte"].dropna().value_counts().head(10).reset_index()
        )
        motivos.columns = ["Motivo", "Quantidade"]
        st.dataframe(motivos, use_container_width=True, hide_index=True)

        st.divider()

    # =========================================================
    # TABELA DETALHADA
    # =========================================================
    st.subheader("Detalhamento dos leads")

    cat_labels = {
        "cadastrado": "Cadastrado no período",
        "ganho": "Ganho no período (cadastrado fora)",
    }
    df_view = df.copy()
    df_view["Categoria"] = df_view["categoria"].map(cat_labels)

    filtro_sdr = st.multiselect(
        "Filtrar por SDR", options=list(SDRS_FOCO.values()),
        default=list(SDRS_FOCO.values()),
    )
    filtro_status = st.multiselect(
        "Filtrar por status atual",
        options=sorted(df_view["stage_atual"].dropna().unique()),
        default=sorted(df_view["stage_atual"].dropna().unique()),
    )

    df_filtrado = df_view[
        df_view["sdr_responsavel"].isin(filtro_sdr)
        & df_view["stage_atual"].isin(filtro_status)
    ].copy()

    df_filtrado["Cadastro"] = df_filtrado["data_cadastro"].dt.strftime("%d/%m/%Y")
    df_filtrado["Atualização"] = df_filtrado["data_atualizacao"].dt.strftime("%d/%m/%Y")

    colunas = [
        "Categoria", "Cadastro", "Atualização", "lead_nome", "stage_atual",
        "sdr_responsavel", "vendedor_atual", "origem",
    ]
    if "stage_descarte" in df_filtrado.columns:
        colunas.extend(["stage_descarte", "motivo_descarte"])

    rename = {
        "lead_nome": "Lead",
        "stage_atual": "Status atual",
        "sdr_responsavel": "SDR",
        "vendedor_atual": "Vendedor",
        "origem": "Origem",
        "stage_descarte": "Etapa descarte",
        "motivo_descarte": "Motivo descarte",
    }
    df_tabela = df_filtrado[colunas].rename(columns=rename).sort_values("Atualização", ascending=False)

    st.caption(f"{len(df_tabela)} leads")
    st.dataframe(df_tabela, use_container_width=True, hide_index=True)

    csv = df_tabela.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Baixar CSV", csv,
        file_name=f"sdr_performance_{data_inicio}_{data_fim}.csv",
        mime="text/csv",
    )
