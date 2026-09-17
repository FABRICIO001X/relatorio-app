"""
Meta semanal das BDRs — visitas marcadas que viraram proposta.

REGRA (definida com o usuário em 16/09/2026):
- "Visita marcada" = lead CADASTRADO pela BDR na semana. A BDR cadastra e
  passa na hora para a consultora, então cadastro = visita marcada.
  Vale tanto o cadastro manual quanto o que chega pela integração do
  discador (origem "3cplus") — nos dois casos o Exact registra quem
  cadastrou na linha do tempo do lead (/ListTimeline/{id}).
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
def _coletar_leads_com_atividade(
    cliente: ExactClient,
    inicio: datetime,
    fim: datetime,
    max_paginas: int = 30,
    page_size: int = 500,
) -> list[dict]:
    """Leads que tiveram atividade na janela (updateDate), o que cobre
    tanto cadastros novos quanto recuperações de leads antigos."""
    ini_iso, fim_iso = inicio.isoformat(), fim.isoformat()
    resultado: list[dict] = []
    vistos = set()
    for i in range(max_paginas):
        params = {
            "$top": page_size,
            "$skip": i * page_size,
            "$orderby": "updateDate desc",
        }
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        passou = False
        for l in itens:
            quando = l.get("updateDate") or ""
            if quando < ini_iso:
                passou = True
                break
            if l["id"] in vistos:
                continue
            vistos.add(l["id"])
            resultado.append(l)
        if passou or len(itens) < page_size:
            break
    return resultado


def _acoes_da_bdr(
    cliente: ExactClient, lead_id: int, inicio_iso: str, fim_iso: str
) -> list[dict]:
    """Eventos de VISITA MARCADA pela BDR dentro da janela, pela linha do tempo.

    Conta dois eventos, os dois registrados com o usuário que fez:
    - "Lead cadastrado"  → lead novo
    - "Lead recuperado"  → lead adormecido/descartado que a BDR reativou
    """
    try:
        resp = cliente._get(f"/ListTimeline/{lead_id}")
    except ExactError:
        return []
    itens = resp.get("value", resp) if isinstance(resp, dict) else resp
    out = []
    for ev in itens or []:
        quando = ev.get("createdAt") or ""
        if not (inicio_iso <= quando <= fim_iso):
            continue
        txt = (ev.get("text") or "").lower()
        uid = (ev.get("user") or {}).get("id")
        if uid not in BDRS:
            continue
        if "lead cadastrado" in txt:
            out.append({"bdr": BDRS[uid], "quando": quando, "tipo": "novo"})
        elif "lead recuperado" in txt:
            out.append({"bdr": BDRS[uid], "quando": quando, "tipo": "recuperado"})
        elif "lead transferido" in txt:
            out.append({"bdr": BDRS[uid], "quando": quando, "tipo": "transferido"})
    return out


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
            "- **Visita marcada:** lead que a BDR **cadastrou** (novo), "
            "**recuperou** (adormecido que ela reativou) ou **qualificou** "
            "(recebeu e passou adiante) na semana — tudo registrado com o nome "
            "dela na linha do tempo\n"
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

    chave = f"meta_semanal_v3:{segunda}:{corte.date()}"
    df = cache.buscar_df(chave, ttl_segundos=ttl_minutos * 60) if usar_cache else None

    if df is None:
        with st.spinner("Buscando leads com atividade na semana..."):
            try:
                leads = _coletar_leads_com_atividade(cliente, inicio_janela, sexta_fim)
            except ExactError as e:
                st.error(f"Erro ao buscar leads: {e}")
                return

        if not leads:
            st.info("Nenhum lead com atividade nessa semana.")
            return

        ini_iso, fim_iso = inicio_janela.isoformat(), sexta_fim.isoformat()
        prog = st.progress(0, text="Lendo a linha do tempo de cada lead...")
        cadastrados: list[tuple[dict, str, str, str]] = []  # (lead, bdr, quando, tipo)
        for i, l in enumerate(leads):
            prog.progress((i + 1) / len(leads))
            for ac in _acoes_da_bdr(cliente, l["id"], ini_iso, fim_iso):
                cadastrados.append((l, ac["bdr"], ac["quando"], ac["tipo"]))
        prog.empty()

        if not cadastrados:
            st.info("Nenhuma visita marcada por BDR nessa semana.")
            return

        with st.spinner("Verificando quais viraram proposta..."):
            try:
                propostas = _coletar_propostas(cliente, inicio_janela)
            except ExactError as e:
                st.error(f"Erro ao buscar propostas: {e}")
                return

        corte_iso = corte.isoformat()
        # Uma visita por lead/BDR na semana; cadastro/recuperação vence transferência
        prioridade = {"novo": 0, "recuperado": 0, "transferido": 1}
        cadastrados.sort(key=lambda x: (x[0]["id"], x[1], prioridade.get(x[3], 9), x[2]))
        rows = []
        vistos_v = set()
        for lead, bdr, quando, tipo in cadastrados:
            lead_id = lead["id"]
            if (lead_id, bdr) in vistos_v:
                continue
            vistos_v.add((lead_id, bdr))
            datas_prop = [
                p for p in propostas.get(lead_id, [])
                if quando < p <= corte_iso
            ]
            converteu = bool(datas_prop)
            rows.append({
                "bdr": bdr,
                "lead_id": lead_id,
                "lead_nome": lead.get("lead") or f"Lead {lead_id}",
                "marcada_em": quando,
                "tipo": tipo,
                "origem": (lead.get("source") or {}).get("value"),
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
        v["Tipo"] = v["tipo"].map({
            "novo": "🆕 Novo", "recuperado": "♻️ Recuperado", "transferido": "↔️ Qualificado",
        })
        v = v[[
            "bdr", "lead_nome", "Tipo", "para", "Marcada em", "Status",
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
