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
def _coletar_leads_cadastrados_periodo(
    cliente: ExactClient,
    data_inicio: date,
    data_fim: date,
    max_paginas: int = 30,
    page_size: int = 500,
) -> list[dict]:
    """
    Coleta leads cadastrados no período (filtrando por registerDate).
    Pagina ordenando por registerDate desc até passar do período.
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
        passou_periodo = False
        for l in itens:
            data_str = (l.get("registerDate") or "")[:10]
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
    lote_size: int = 30,
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


def _coletar_descartes_por_lead(
    cliente: ExactClient,
    lead_ids: set[int],
    max_paginas: int = 30,
    page_size: int = 500,
) -> dict[int, dict]:
    """
    Pagina /Losts e devolve mapa {lead_id: {'stage': str, 'reason': str, 'date': str}}
    apenas pros lead_ids passados.

    Para cada lead, mantém o descarte MAIS RECENTE caso haja múltiplos
    (improvável, mas teoricamente possível).
    """
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
        # Se já temos todos os leads que nos interessam, podemos parar
        if len(descartes) >= len(lead_ids):
            break
    return descartes


# =============================================================
# Processamento
# =============================================================
def _construir_dataframe(
    transferencias: list[dict],
    leads_completos: dict[int, dict],
    sdrs_foco: dict[int, str],
    descartes_por_lead: dict[int, dict] | None = None,
    leads_cadastrados_periodo: list[dict] | None = None,
) -> pd.DataFrame:
    """
    NOVA LÓGICA:
    Cada lead cadastrado no período (leads_cadastrados_periodo) vira UMA linha.
    A 'sdr_responsavel' vem da PRIMEIRA transferência feita por uma das SDRs foco
    (em TODO o histórico, não só do período).

    Isso garante que:
    - Leads são contados pelo registerDate (período de cadastro)
    - O dono é a SDR original que cadastrou e transferiu (mesmo que tenha sido
      transferida fora do período)

    Se `leads_cadastrados_periodo` for None, cai no modo antigo (transferências).
    """
    descartes_por_lead = descartes_por_lead or {}

    # Mapa: leadId -> primeira transferência feita por SDR foco
    transf_asc = sorted(transferencias, key=lambda t: t.get("createdAt") or "")
    primeira_transf_sdr: dict[int, dict] = {}
    for t in transf_asc:
        lead_id = t.get("leadId")
        if not lead_id:
            continue
        if t.get("originUserId") not in sdrs_foco:
            continue
        if lead_id in primeira_transf_sdr:
            continue
        primeira_transf_sdr[lead_id] = t

    rows = []

    if leads_cadastrados_periodo is not None:
        # MODO NOVO: leads cadastrados no período como base
        for lead in leads_cadastrados_periodo:
            lead_id = lead.get("id")
            t = primeira_transf_sdr.get(lead_id)
            if not t:
                # Lead cadastrado no período mas não foi transferido por nenhuma das 3 SDRs
                continue
            sdr_nome = sdrs_foco[t["originUserId"]]
            # Sobrescreve com lead completo se tiver
            lead_full = leads_completos.get(lead_id, lead)
            sales = lead_full.get("salesRep") or {}
            source = lead_full.get("source") or {}
            descarte = descartes_por_lead.get(lead_id) or {}
            rows.append({
                "lead_id": lead_id,
                "lead_nome": lead_full.get("lead"),
                "stage_atual": lead_full.get("stage"),
                "sdr_responsavel": sdr_nome,
                "vendedor_atual": sales.get("name") if sales.get("id") else None,
                "origem": source.get("value"),
                "data_cadastro": lead_full.get("registerDate"),
                "data_transferencia": t.get("createdAt"),
                "data_atualizacao": lead_full.get("updateDate"),
                "stage_descarte": descarte.get("stage"),
                "motivo_descarte": descarte.get("reason"),
            })
    else:
        # MODO ANTIGO: transferências como base (mantido por retrocompatibilidade)
        for lead_id, t in primeira_transf_sdr.items():
            sdr_nome = sdrs_foco[t["originUserId"]]
            lead = leads_completos.get(lead_id)
            if not lead:
                continue
            sales = lead.get("salesRep") or {}
            source = lead.get("source") or {}
            descarte = descartes_por_lead.get(lead_id) or {}
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
                "stage_descarte": descarte.get("stage"),
                "motivo_descarte": descarte.get("reason"),
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for col in ["data_cadastro", "data_transferencia", "data_atualizacao"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    df["dias_no_funil"] = (df["data_atualizacao"] - df["data_transferencia"]).dt.days
    return df


def _calcular_metricas(
    df_sdr: pd.DataFrame,
    data_inicio: date,
    data_fim: date,
) -> dict[str, Any]:
    """
    Calcula métricas resumidas pra um SDR.

    REGRAS:
    - Leads (total) = todas as transferências da SDR no período
    - Em andamento = leads ainda em status inicial AGORA (independente da data)
    - Propostas/Ganhos/Descartes = só conta os que MUDARAM PARA esse status
      dentro do período (usa updateDate como proxy de quando virou aquele status)
    """
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

    # Em andamento = status atual ainda inicial
    em_andamento = df_sdr["stage_atual"].isin(STAGES_INICIAIS).sum()

    # Pra propostas/ganhos/descartes: filtrar pela data em que mudou de status
    # Usamos updateDate como proxy (última modificação do lead)
    di = pd.Timestamp(data_inicio).tz_localize("UTC")
    df_fim = pd.Timestamp(data_fim).tz_localize("UTC") + pd.Timedelta(days=1)

    em_status_no_periodo = (
        (df_sdr["data_atualizacao"] >= di) & (df_sdr["data_atualizacao"] < df_fim)
    )

    propostas = ((df_sdr["stage_atual"] == STAGE_PROPOSTA) & em_status_no_periodo).sum()
    ganhos = ((df_sdr["stage_atual"] == STAGE_GANHO) & em_status_no_periodo).sum()
    descartes = ((df_sdr["stage_atual"] == STAGE_DESCARTE) & em_status_no_periodo).sum()

    # Tempo médio até ganho (entre transferência e fechamento)
    df_ganhos = df_sdr[
        (df_sdr["stage_atual"] == STAGE_GANHO) & em_status_no_periodo
    ]
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
            "**Total de leads:** leads CADASTRADOS no Spotter dentro do período "
            "(data de criação do lead). A SDR responsável é quem fez a primeira "
            "transferência pro vendedor.\n\n"
            "**Ganhos / Propostas / Descartes:** contados pelo mês em que o lead "
            "MUDOU para esse status. Ex: lead cadastrado em abril e fechado em maio "
            "aparece em **Total de leads de abril** e em **Ganhos de maio**.\n\n"
            "**Em andamento:** leads ainda em status inicial agora "
            "(BDR, Tentativa, Sem contato)."
        )

    try:
        cliente = ExactClient()
    except ExactError as e:
        st.error(f"Token do Exact não configurado: {e}")
        return

    chave = f"sdr_v3:{data_inicio}:{data_fim}"
    df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df is None:
        # NOVA LÓGICA:
        # 1) Buscar leads CADASTRADOS no período (registerDate dentro do período)
        # 2) Buscar transferências de todo histórico (sem filtro de data) pra
        #    identificar a SDR que cadastrou cada lead
        # 3) Cruzar tudo

        with st.spinner(f"Buscando leads cadastrados de {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}..."):
            try:
                leads_periodo = _coletar_leads_cadastrados_periodo(
                    cliente, data_inicio, data_fim
                )
            except ExactError as e:
                st.error(f"Erro ao buscar leads: {e}")
                return

        if not leads_periodo:
            st.info("Nenhum lead cadastrado no período.")
            return

        st.caption(f"Encontrados {len(leads_periodo)} leads cadastrados no período.")

        # Buscar transferências (histórico amplo: 90 dias antes do início pra garantir)
        from datetime import timedelta
        di_busca = data_inicio - timedelta(days=90)

        with st.spinner(f"Identificando SDR de cada lead (transferências desde {di_busca.strftime('%d/%m/%Y')})..."):
            try:
                transferencias = _coletar_transferencias(cliente, di_busca, data_fim)
            except ExactError as e:
                st.error(f"Erro ao buscar transferências: {e}")
                return

        # Lead completos: já temos os leads do período, mas precisamos garantir
        # que temos o status atual atualizado (que já vem no /Leads, então OK)
        leads_completos = {l["id"]: l for l in leads_periodo}
        lead_ids_periodo = list(leads_completos.keys())

        with st.spinner("Buscando descartes (etapa onde lead foi perdido)..."):
            try:
                descartes_por_lead = _coletar_descartes_por_lead(cliente, set(lead_ids_periodo))
            except ExactError as e:
                st.warning(f"Não consegui buscar descartes: {e}")
                descartes_por_lead = {}

        df = _construir_dataframe(
            transferencias,
            leads_completos,
            SDRS_FOCO,
            descartes_por_lead,
            leads_cadastrados_periodo=leads_periodo,
        )
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
        m = _calcular_metricas(df_sdr, data_inicio, data_fim)
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
        m = _calcular_metricas(df_sdr, data_inicio, data_fim)
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
    # GRÁFICO — etapa onde os leads foram descartados
    # =========================================================
    if "stage_descarte" in df.columns:
        df_descartes = df[df["stage_descarte"].notna()].copy()
        if not df_descartes.empty:
            st.subheader("📉 Em qual etapa os leads foram descartados")
            st.caption(
                "Mostra em qual etapa do funil cada lead foi perdido. "
                "Descartes em 'BDR' / 'TENTATIVA DE CONTATO' indicam leads pouco "
                "qualificados. Descartes em 'PROPOSTA ENVIADA' indicam perdas "
                "na negociação."
            )

            # Agrupar por SDR + etapa de descarte
            grouped = (
                df_descartes.groupby(["sdr_responsavel", "stage_descarte"])
                .size()
                .reset_index(name="Quantidade")
                .rename(columns={
                    "sdr_responsavel": "SDR",
                    "stage_descarte": "Etapa do descarte",
                })
            )
            fig_desc = px.bar(
                grouped,
                x="SDR",
                y="Quantidade",
                color="Etapa do descarte",
                barmode="group",
                title="Descartes por etapa do funil",
                category_orders={
                    "Etapa do descarte": [
                        "BDR", "SEM CONTATO", "TENTATIVA DE CONTATO",
                        "PROPOSTA ENVIADA", "NEGOCIO FECHADO",
                    ],
                },
                color_discrete_map={
                    "BDR": "#C8C8C8",
                    "SEM CONTATO": "#9F9F9F",
                    "TENTATIVA DE CONTATO": "#E5A663",
                    "PROPOSTA ENVIADA": "#D85A30",
                    "NEGOCIO FECHADO": "#7B3FB3",
                },
            )
            st.plotly_chart(fig_desc, use_container_width=True)

            # Top motivos de descarte
            st.markdown("**Top motivos de descarte (todas as SDRs juntas)**")
            motivos = (
                df_descartes["motivo_descarte"]
                .dropna()
                .value_counts()
                .head(10)
                .reset_index()
            )
            motivos.columns = ["Motivo", "Quantidade"]
            st.dataframe(motivos, use_container_width=True, hide_index=True)

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

    # Data ganho/perda = data de atualização do lead (quando virou ganho/descarte)
    # Só mostra se o status atual é Ganho ou Descartado
    df_view["Data ganho/perda"] = df_view.apply(
        lambda row: row["data_atualizacao"].strftime("%d/%m/%Y")
        if pd.notna(row["data_atualizacao"]) and row["stage_atual"] in [STAGE_GANHO, STAGE_DESCARTE]
        else "",
        axis=1,
    )

    colunas_view = [
        "Transferência", "lead_nome", "stage_atual", "Data ganho/perda",
        "sdr_responsavel", "vendedor_atual", "origem", "dias_no_funil",
    ]
    rename_map = {
        "lead_nome": "Lead",
        "stage_atual": "Status atual",
        "sdr_responsavel": "SDR (cadastrou)",
        "vendedor_atual": "Vendedor atual",
        "origem": "Origem",
        "dias_no_funil": "Dias",
    }
    # Adicionar colunas de descarte se existirem
    if "stage_descarte" in df_view.columns:
        colunas_view.extend(["stage_descarte", "motivo_descarte"])
        rename_map["stage_descarte"] = "Etapa do descarte"
        rename_map["motivo_descarte"] = "Motivo descarte"

    df_view = df_view[colunas_view].rename(columns=rename_map).sort_values(
        "Transferência", ascending=False
    )

    st.caption(f"{len(df_view)} leads filtrados")
    st.dataframe(df_view, use_container_width=True, hide_index=True)

    csv = df_view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Baixar CSV",
        csv,
        file_name=f"sdr_performance_{data_inicio}_{data_fim}.csv",
        mime="text/csv",
    )
