"""
Comissões dos BDRs (SDRs) — regra da janela de elegibilidade.

REGRA (definida com o usuário em 11/09/2026):
- Paga-se comissão FIXA por venda fechada.
- A venda só é elegível se o lead foi cadastrado há no máximo N meses
  em relação à data do fechamento (padrão: 3 meses / 90 dias).
- Isso evita pagar por lead antigo que virou venda muito tempo depois.

SDR responsável: quem fez a primeira transferência do lead (transferHistory),
mesmo critério já usado na aba SDRs.
"""
from __future__ import annotations

from datetime import date
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
NOMES_SDRS = list(SDRS_FOCO.values())

STAGE_GANHO = "NEGOCIO FECHADO"
STAGE_SEM_CONTATO = "SEM CONTATO"

VALOR_COMISSAO_PADRAO = 50.0
JANELA_MESES_PADRAO = 3

CORES_SDR = {
    "IASMIM": "#1D9E75",
    "CRISLANE": "#378ADD",
    "JENNYFER": "#E5A663",
}


def _brl(valor: float) -> str:
    """Formata número como moeda brasileira."""
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# =============================================================
# Coleta
# =============================================================
def _coletar_stages_dos_leads(
    cliente: ExactClient,
    lead_ids: list[int],
    lote_size: int = 25,
) -> dict[int, list[dict]]:
    """Histórico de etapas dos leads, agrupado por leadId (ordem cronológica)."""
    por_lead: dict[int, list[dict]] = {}
    for i in range(0, len(lead_ids), lote_size):
        lote = lead_ids[i : i + lote_size]
        ids_str = ",".join(str(x) for x in lote)
        params = {
            "$filter": f"leadId in ({ids_str})",
            "$top": 500,
            "$orderby": "createdAt asc",
        }
        resp = cliente._get("/leadStages", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        for s in itens or []:
            por_lead.setdefault(s["leadId"], []).append(s)
    return por_lead


def _data_base_elegibilidade(
    lead: dict,
    stages: list[dict],
) -> tuple[str, str]:
    """Retorna (data_base_iso, tipo) para contar a janela de comissão.

    Se o lead foi REATIVADO (saiu de SEM CONTATO) antes do fechamento,
    a janela conta a partir da reativação — não do cadastro original.
    Leads adormecidos que a equipe reativou geram comissão.
    """
    fechamento = None
    for s in stages:
        if s.get("destinationStage") == STAGE_GANHO:
            fechamento = s.get("createdAt")

    reativacao = None
    for s in stages:
        if s.get("originStage") == STAGE_SEM_CONTATO:
            if fechamento is None or (s.get("createdAt") or "") < fechamento:
                reativacao = s.get("createdAt")

    if reativacao:
        return reativacao, "reativado"
    return lead.get("registerDate") or "", "novo"


def _coletar_ganhos(
    cliente: ExactClient,
    data_inicio: date,
    data_fim: date,
    max_paginas: int = 30,
    page_size: int = 500,
) -> list[dict]:
    """Leads que estão em NEGOCIO FECHADO com updateDate dentro do período."""
    todos: list[dict] = []
    di, df_ = data_inicio.isoformat(), data_fim.isoformat()
    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$orderby": "updateDate desc",
            "$filter": f"stage eq '{STAGE_GANHO}'",
        }
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        passou = False
        for l in itens:
            d = (l.get("updateDate") or "")[:10]
            if d < di:
                passou = True
                break
            if d > df_:
                continue
            todos.append(l)
        if passou or len(itens) < page_size:
            break
    return todos


def _mapear_sdr(
    cliente: ExactClient,
    lead_ids: set[int],
    max_paginas: int = 50,
    page_size: int = 500,
) -> dict[int, int]:
    """Mapa {lead_id: sdr_id} pela PRIMEIRA transferência feita por uma SDR foco."""
    primeira: dict[int, int] = {}
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
            if origin in sdr_ids and lid not in primeira:
                primeira[lid] = origin
        if len(itens) < page_size:
            break
        if len(primeira) >= len(lead_ids):
            break
    return primeira


# =============================================================
# Renderização
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    st.markdown("### 💰 Comissões dos BDRs")
    st.caption(
        f"Vendas fechadas de {data_inicio.strftime('%d/%m/%Y')} "
        f"a {data_fim.strftime('%d/%m/%Y')}"
    )

    # --- Parâmetros da regra ---
    c1, c2 = st.columns(2)
    with c1:
        valor_comissao = st.number_input(
            "Valor por venda (R$)",
            min_value=0.0,
            value=VALOR_COMISSAO_PADRAO,
            step=10.0,
            help="Valor fixo pago ao BDR por cada venda elegível.",
        )
    with c2:
        janela_meses = st.number_input(
            "Janela de elegibilidade (meses)",
            min_value=1,
            max_value=24,
            value=JANELA_MESES_PADRAO,
            step=1,
            help="A venda só conta se o lead foi cadastrado dentro dessa janela "
                 "antes do fechamento.",
        )
    janela_dias = int(janela_meses) * 30

    with st.expander("ℹ️ Como a comissão é calculada"):
        st.markdown(
            f"- **Base:** leads que fecharam venda (`{STAGE_GANHO}`) dentro do "
            "período selecionado na barra lateral\n"
            f"- **Regra dos {janela_meses} meses:** a venda é elegível se o lead "
            f"entrou em contato há no máximo **{janela_dias} dias** do fechamento\n"
            "- **Lead novo:** conta a partir do cadastro\n"
            "- **Lead reativado:** conta a partir da **reativação** — quando o lead "
            f"saiu de `{STAGE_SEM_CONTATO}` e voltou a ser trabalhado. "
            "Lead adormecido de meses atrás que a equipe reativou e fechou "
            "**gera comissão**\n"
            f"- **Valor:** {_brl(valor_comissao)} por venda elegível\n"
            "- **BDR responsável:** quem fez a primeira transferência do lead "
            "(inclui quem recebeu, qualificou e devolveu)"
        )

    try:
        cliente = ExactClient()
    except ExactError as e:
        st.error(f"Token do Exact não configurado: {e}")
        return

    chave = f"comissoes_bdr_v1:{data_inicio}:{data_fim}"
    df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df is None:
        with st.spinner("Buscando vendas fechadas no período..."):
            try:
                ganhos = _coletar_ganhos(cliente, data_inicio, data_fim)
            except ExactError as e:
                st.error(f"Erro ao buscar vendas: {e}")
                return

        if not ganhos:
            st.info("Nenhuma venda fechada no período.")
            return

        ids = {l["id"] for l in ganhos}
        with st.spinner(f"Identificando o BDR de {len(ids)} vendas..."):
            try:
                sdr_por_lead = _mapear_sdr(cliente, ids)
            except ExactError as e:
                st.error(f"Erro ao buscar transferências: {e}")
                return

        # Histórico de etapas: precisamos saber se o lead foi reativado
        ids_com_bdr = [l["id"] for l in ganhos if sdr_por_lead.get(l["id"]) is not None]
        with st.spinner("Verificando reativações..."):
            try:
                stages_por_lead = _coletar_stages_dos_leads(cliente, ids_com_bdr)
            except ExactError as e:
                st.error(f"Erro ao buscar histórico de etapas: {e}")
                return

        rows = []
        for l in ganhos:
            sdr_id = sdr_por_lead.get(l["id"])
            if sdr_id is None:
                continue  # venda sem BDR foco — não entra na comissão
            stages = stages_por_lead.get(l["id"], [])
            data_base, tipo = _data_base_elegibilidade(l, stages)
            rows.append({
                "lead_id": l["id"],
                "lead_nome": l.get("lead"),
                "bdr": SDRS_FOCO[sdr_id],
                "data_cadastro": l.get("registerDate"),
                "data_base": data_base,
                "tipo_lead": tipo,
                "data_ganho": l.get("updateDate"),
            })

        df = pd.DataFrame(rows)
        cache.salvar_df(chave, df)

    if df.empty:
        st.info("Nenhuma venda com BDR identificado no período.")
        return

    # --- Calcular elegibilidade (feito fora do cache: depende dos sliders) ---
    df = df.copy()
    df["dt_cadastro"] = pd.to_datetime(df["data_cadastro"], errors="coerce", utc=True)
    df["dt_ganho"] = pd.to_datetime(df["data_ganho"], errors="coerce", utc=True)
    if "data_base" not in df.columns:
        df["data_base"] = df["data_cadastro"]
        df["tipo_lead"] = "novo"
    df["dt_base"] = pd.to_datetime(df["data_base"], errors="coerce", utc=True)
    df["dt_base"] = df["dt_base"].fillna(df["dt_cadastro"])
    df["dias_ate_fechar"] = (df["dt_ganho"] - df["dt_base"]).dt.days
    df["elegivel"] = df["dias_ate_fechar"].notna() & (df["dias_ate_fechar"] <= janela_dias)

    total_vendas = len(df)
    elegiveis = df[df["elegivel"]]
    fora = df[~df["elegivel"]]
    valor_total = len(elegiveis) * valor_comissao

    # =========================================================
    # Resumo geral
    # =========================================================
    reativados_ok = int((elegiveis["tipo_lead"] == "reativado").sum()) \
        if "tipo_lead" in elegiveis.columns else 0

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Vendas no período", total_vendas)
    k2.metric("Elegíveis ✅", len(elegiveis))
    k3.metric(
        "Por reativação ♻️", reativados_ok,
        help="Leads adormecidos que a equipe reativou e fechou dentro da janela",
    )
    k4.metric("Fora da janela ❌", len(fora))
    k5.metric("Total a pagar", _brl(valor_total))

    st.divider()

    # =========================================================
    # Por BDR
    # =========================================================
    st.subheader("Comissão por BDR")

    linhas = []
    for nome in NOMES_SDRS:
        d_all = df[df["bdr"] == nome]
        d_ok = elegiveis[elegiveis["bdr"] == nome]
        linhas.append({
            "BDR": nome,
            "Vendas": len(d_all),
            "Elegíveis": len(d_ok),
            "Fora da janela": len(d_all) - len(d_ok),
            "_valor": len(d_ok) * valor_comissao,
        })
    resumo = pd.DataFrame(linhas)

    cols = st.columns(3)
    for i, nome in enumerate(NOMES_SDRS):
        r = resumo[resumo["BDR"] == nome].iloc[0]
        with cols[i]:
            st.markdown(f"#### {nome}")
            st.metric("A pagar", _brl(r["_valor"]))
            a, b = st.columns(2)
            a.metric("Elegíveis", int(r["Elegíveis"]))
            b.metric("Fora", int(r["Fora da janela"]))

    tabela = resumo.copy()
    tabela["Comissão"] = tabela["_valor"].apply(_brl)
    tabela = tabela.drop(columns=["_valor"])
    st.dataframe(tabela, use_container_width=True, hide_index=True)

    if valor_total > 0:
        fig = px.bar(
            resumo, x="BDR", y="_valor", color="BDR", text=resumo["_valor"].apply(_brl),
            title="Valor a pagar por BDR", color_discrete_map=CORES_SDR,
            labels={"_valor": "Comissão (R$)"},
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # =========================================================
    # Detalhamento
    # =========================================================
    aba_ok, aba_fora = st.tabs([
        f"✅ Elegíveis ({len(elegiveis)})",
        f"❌ Fora da janela ({len(fora)})",
    ])

    def montar_tabela(d: pd.DataFrame) -> pd.DataFrame:
        v = d.copy()
        v["Cadastrado"] = v["dt_cadastro"].dt.strftime("%d/%m/%Y")
        v["Contato"] = v["dt_base"].dt.strftime("%d/%m/%Y")
        v["Fechado"] = v["dt_ganho"].dt.strftime("%d/%m/%Y")
        v["Tipo"] = v.get("tipo_lead", "novo").map(
            {"reativado": "♻️ Reativado", "novo": "🆕 Novo"}
        ).fillna("🆕 Novo")
        return v[[
            "bdr", "lead_nome", "Tipo", "Cadastrado", "Contato",
            "Fechado", "dias_ate_fechar",
        ]].rename(
            columns={
                "bdr": "BDR",
                "lead_nome": "Lead",
                "dias_ate_fechar": "Dias até fechar",
            }
        ).sort_values("Dias até fechar")

    with aba_ok:
        filtro = st.multiselect(
            "Filtrar BDR", options=NOMES_SDRS, default=NOMES_SDRS, key="f_com_ok"
        )
        d = elegiveis[elegiveis["bdr"].isin(filtro)]
        st.markdown(
            f"**{len(d)} vendas · {_brl(len(d) * valor_comissao)} a pagar**"
        )
        if d.empty:
            st.info("Nenhuma venda elegível para os BDRs selecionados.")
        else:
            st.dataframe(montar_tabela(d), use_container_width=True, hide_index=True)
            st.download_button(
                "📥 Baixar CSV",
                montar_tabela(d).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"comissoes_elegiveis_{data_inicio}_{data_fim}.csv",
                mime="text/csv", key="dl_com_ok",
            )

    with aba_fora:
        st.caption(
            f"Vendas fechadas no período cujo último contato (cadastro ou "
            f"reativação) foi há mais de {janela_dias} dias — não geram comissão."
        )
        if fora.empty:
            st.success("Nenhuma venda ficou fora da janela.")
        else:
            st.dataframe(
                montar_tabela(fora), use_container_width=True, hide_index=True
            )
            st.download_button(
                "📥 Baixar CSV",
                montar_tabela(fora).to_csv(index=False).encode("utf-8-sig"),
                file_name=f"comissoes_fora_{data_inicio}_{data_fim}.csv",
                mime="text/csv", key="dl_com_fora",
            )
