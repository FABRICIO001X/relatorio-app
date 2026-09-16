"""
Meta semanal das BDRs — visitas marcadas que viraram proposta.

REGRA (definida com o usuário em 16/09/2026):
- "Visita marcada" = a BDR transfere o lead para uma consultora.
- A semana de marcação vai de SEGUNDA a SEXTA.
- A apuração é na SEGUNDA seguinte, para dar tempo das visitas de sexta
  andarem no fim de semana.
- Conta a visita que chegou em PROPOSTA ENVIADA depois da transferência
  e até o dia da apuração. O caminho no meio não importa: o lead pode
  passar por outras etapas antes de virar proposta.
- Vence a BDR com mais conversões (número absoluto).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from connectors.exact import ExactClient, ExactError
from db import cache

BDRS = {
    436128: "IASMIM",
    442056: "CRISLANE",
    448464: "JENNYFER",
}
NOMES_BDRS = list(BDRS.values())

STAGE_PROPOSTA = "PROPOSTA ENVIADA"

CORES = {
    "IASMIM": "#1D9E75",
    "CRISLANE": "#378ADD",
    "JENNYFER": "#E5A663",
}

MEDALHAS = ["🥇", "🥈", "🥉"]


# =============================================================
# Semanas
# =============================================================
def _segunda_da_semana(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _semanas_disponiveis(qtd: int = 8) -> list[date]:
    """Segundas-feiras das últimas `qtd` semanas, da mais recente para trás."""
    seg_atual = _segunda_da_semana(date.today())
    return [seg_atual - timedelta(weeks=i) for i in range(qtd)]


def _rotulo_semana(segunda: date) -> str:
    sexta = segunda + timedelta(days=4)
    hoje = date.today()
    base = f"{segunda.strftime('%d/%m')} a {sexta.strftime('%d/%m')}"
    if segunda == _segunda_da_semana(hoje):
        return f"{base} (em curso)"
    return base


# =============================================================
# Coleta
# =============================================================
def _coletar_transferencias(
    cliente: ExactClient,
    inicio: datetime,
    fim: datetime,
    max_paginas: int = 60,
    page_size: int = 500,
) -> list[dict]:
    """Transferências feitas por BDRs dentro da janela."""
    ini_iso, fim_iso = inicio.isoformat(), fim.isoformat()
    resultado: list[dict] = []
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
        passou = False
        for t in itens:
            quando = t.get("createdAt") or ""
            if quando < ini_iso:
                passou = True
                break
            if quando > fim_iso:
                continue
            if t.get("originUserId") in BDRS:
                resultado.append(t)
        if passou or len(itens) < page_size:
            break
    return resultado


def _coletar_propostas(
    cliente: ExactClient,
    desde: datetime,
    max_paginas: int = 40,
    page_size: int = 500,
) -> dict[int, list[str]]:
    """Mapa {lead_id: [datas em que entrou em PROPOSTA ENVIADA]}."""
    desde_iso = desde.isoformat()
    por_lead: dict[int, list[str]] = {}
    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$filter": f"destinationStage eq '{STAGE_PROPOSTA}'",
            "$orderby": "createdAt desc",
        }
        resp = cliente._get("/leadStages", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        passou = False
        for s in itens:
            quando = s.get("createdAt") or ""
            if quando < desde_iso:
                passou = True
                break
            por_lead.setdefault(s["leadId"], []).append(quando)
        if passou or len(itens) < page_size:
            break
    return por_lead


def _nomes_dos_leads(
    cliente: ExactClient,
    lead_ids: list[int],
    lote: int = 25,
) -> dict[int, dict]:
    info: dict[int, dict] = {}
    for i in range(0, len(lead_ids), lote):
        parte = lead_ids[i : i + lote]
        ids_str = ",".join(str(x) for x in parte)
        resp = cliente._get(
            "/Leads", params={"$filter": f"id in ({ids_str})", "$top": lote}
        )
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        for l in itens or []:
            info[l["id"]] = l
    return info


# =============================================================
# Renderização
# =============================================================
def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    st.markdown("### 🎯 Meta semanal das BDRs")
    st.caption("Visitas marcadas de segunda a sexta que viraram proposta")

    semanas = _semanas_disponiveis(8)
    escolha = st.selectbox(
        "Semana",
        options=semanas,
        format_func=_rotulo_semana,
        index=0,
    )

    segunda = escolha
    sexta_fim = datetime.combine(
        segunda + timedelta(days=4), datetime.max.time(), tzinfo=timezone.utc
    )
    inicio_janela = datetime.combine(segunda, datetime.min.time(), tzinfo=timezone.utc)
    # Apuração: segunda seguinte, fim do dia
    apuracao = datetime.combine(
        segunda + timedelta(days=7), datetime.max.time(), tzinfo=timezone.utc
    )
    agora = datetime.now(timezone.utc)
    corte = min(apuracao, agora)
    em_curso = corte < apuracao

    with st.expander("ℹ️ Como a meta é apurada"):
        st.markdown(
            "- **Visita marcada:** quando a BDR transfere o lead para uma "
            "consultora\n"
            f"- **Semana de marcação:** {segunda.strftime('%d/%m')} (segunda) a "
            f"{(segunda + timedelta(days=4)).strftime('%d/%m')} (sexta)\n"
            f"- **Apuração:** {(segunda + timedelta(days=7)).strftime('%d/%m')} "
            "(segunda seguinte), para dar tempo das visitas de sexta andarem\n"
            f"- **Converteu:** o lead entrou em `{STAGE_PROPOSTA}` depois da "
            "transferência e até a apuração. Pode ter passado por outras etapas "
            "no caminho\n"
            "- **Vence** a BDR com mais visitas convertidas"
        )

    try:
        cliente = ExactClient()
    except ExactError as e:
        st.error(f"Token do Exact não configurado: {e}")
        return

    chave = f"meta_semanal_v1:{segunda}:{corte.date()}"
    df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df is None:
        with st.spinner("Buscando visitas marcadas na semana..."):
            try:
                transfs = _coletar_transferencias(cliente, inicio_janela, sexta_fim)
            except ExactError as e:
                st.error(f"Erro ao buscar transferências: {e}")
                return

        if not transfs:
            st.info("Nenhuma visita marcada por BDR nessa semana.")
            return

        with st.spinner("Verificando quais viraram proposta..."):
            try:
                propostas = _coletar_propostas(cliente, inicio_janela)
            except ExactError as e:
                st.error(f"Erro ao buscar propostas: {e}")
                return

        ids = [t["leadId"] for t in transfs]
        with st.spinner(f"Buscando dados de {len(set(ids))} leads..."):
            try:
                info = _nomes_dos_leads(cliente, list(set(ids)))
            except ExactError as e:
                st.error(f"Erro ao buscar leads: {e}")
                return

        corte_iso = corte.isoformat()
        rows = []
        vistos = set()
        for t in transfs:
            lead_id = t["leadId"]
            bdr = BDRS[t["originUserId"]]
            quando = t.get("createdAt") or ""
            # uma visita por lead/BDR na semana
            chave_v = (lead_id, bdr)
            if chave_v in vistos:
                continue
            vistos.add(chave_v)

            # proposta depois da transferência e até a apuração
            datas_prop = [
                p for p in propostas.get(lead_id, [])
                if quando < p <= corte_iso
            ]
            converteu = bool(datas_prop)
            lead = info.get(lead_id, {})
            rows.append({
                "bdr": bdr,
                "lead_id": lead_id,
                "lead_nome": lead.get("lead") or f"Lead {lead_id}",
                "marcada_em": quando,
                "para": (lead.get("sdr") or {}).get("name"),
                "converteu": converteu,
                "proposta_em": min(datas_prop) if datas_prop else None,
                "etapa_atual": lead.get("stage"),
            })

        df = pd.DataFrame(rows)
        cache.salvar_df(chave, df)

    if df.empty:
        st.info("Nenhuma visita marcada por BDR nessa semana.")
        return

    # =========================================================
    # Placar
    # =========================================================
    placar = []
    for nome in NOMES_BDRS:
        d = df[df["bdr"] == nome]
        conv = int(d["converteu"].sum())
        marcadas = len(d)
        placar.append({
            "BDR": nome,
            "Visitas marcadas": marcadas,
            "_conv": conv,
            "_taxa": (conv / marcadas * 100) if marcadas else 0.0,
        })

    df_placar = (
        pd.DataFrame(placar)
        .sort_values(["_conv", "_taxa"], ascending=[False, False])
        .reset_index(drop=True)
    )

    if em_curso:
        st.info(
            f"Semana em andamento. Apuração final em "
            f"{(segunda + timedelta(days=7)).strftime('%d/%m')}."
        )

    lider = df_placar.iloc[0]
    empate = (df_placar["_conv"] == lider["_conv"]).sum() > 1

    if lider["_conv"] > 0 and not empate:
        st.success(
            f"🏆 **{lider['BDR']}** — {int(lider['_conv'])} visitas viraram proposta"
            + (" (parcial)" if em_curso else "")
        )
    elif empate and lider["_conv"] > 0:
        empatadas = df_placar[df_placar["_conv"] == lider["_conv"]]["BDR"].tolist()
        st.warning(
            f"🤝 Empate entre **{' e '.join(empatadas)}** — "
            f"{int(lider['_conv'])} cada"
        )

    cols = st.columns(3)
    for i, row in df_placar.iterrows():
        with cols[i]:
            medalha = MEDALHAS[i] if i < 3 else ""
            st.markdown(f"#### {medalha} {row['BDR']}")
            st.metric("Viraram proposta", int(row["_conv"]))
            a, b = st.columns(2)
            a.metric("Marcadas", int(row["Visitas marcadas"]))
            b.metric("Taxa", f"{row['_taxa']:.0f}%")

    st.divider()

    tabela = df_placar.copy()
    tabela.insert(0, "#", [MEDALHAS[i] if i < 3 else str(i + 1) for i in range(len(tabela))])
    tabela["Viraram proposta"] = tabela["_conv"]
    tabela["Taxa"] = tabela["_taxa"].apply(lambda x: f"{x:.0f}%")
    st.dataframe(
        tabela[["#", "BDR", "Visitas marcadas", "Viraram proposta", "Taxa"]],
        use_container_width=True, hide_index=True,
    )

    if df_placar["_conv"].sum() > 0:
        fig = px.bar(
            df_placar, x="BDR", y="_conv", color="BDR", text="_conv",
            title="Visitas que viraram proposta",
            labels={"_conv": "Convertidas"},
            color_discrete_map=CORES,
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # =========================================================
    # Detalhamento
    # =========================================================
    st.subheader("Detalhamento das visitas")

    filtro = st.multiselect(
        "Filtrar BDR", options=NOMES_BDRS, default=NOMES_BDRS, key="f_meta"
    )
    so_conv = st.checkbox("Mostrar só as que viraram proposta", value=False)

    d = df[df["bdr"].isin(filtro)]
    if so_conv:
        d = d[d["converteu"]]

    if d.empty:
        st.info("Nenhuma visita com esses filtros.")
    else:
        v = d.copy()
        v["Marcada em"] = pd.to_datetime(v["marcada_em"], errors="coerce").dt.strftime("%d/%m %H:%M")
        v["Proposta em"] = pd.to_datetime(v["proposta_em"], errors="coerce").dt.strftime("%d/%m %H:%M")
        v["Proposta em"] = v["Proposta em"].fillna("—")
        v["Status"] = v["converteu"].map({True: "✅ Virou proposta", False: "⏳ Ainda não"})
        v = v[[
            "bdr", "lead_nome", "para", "Marcada em", "Status",
            "Proposta em", "etapa_atual",
        ]].rename(columns={
            "bdr": "BDR",
            "lead_nome": "Lead",
            "para": "Consultora",
            "etapa_atual": "Etapa atual",
        }).sort_values("Marcada em")
        st.dataframe(v, use_container_width=True, hide_index=True)
        st.download_button(
            "📥 Baixar CSV",
            v.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"meta_semanal_{segunda}.csv",
            mime="text/csv", key="dl_meta",
        )
