"""
Visão geral — KPIs do dia e da operação.

Busca dados ao vivo (não depende das outras abas terem sido visitadas).
Tudo em consultas leves e agregadas para abrir rápido.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from connectors.exact import ExactClient, ExactError
from connectors.tres_c_plus import TresCPlusClient, TresCPlusError
from db import cache

SDRS_3C = {
    "IASMIM": 123722,
    "CRISLANE": 200133,
    "JENNYFER": 223044,
    "DANIELE": 102589,
    "LAIANE": 60516,
    "LAYLA": 183640,
}

STAGE_PROPOSTA = "PROPOSTA ENVIADA"
STAGE_GANHO = "NEGOCIO FECHADO"

VALOR_COMISSAO = 50.0
JANELA_DIAS = 90


def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _contar_leadstages(cliente: ExactClient, destino: str, dia: date) -> int:
    """Conta entradas numa etapa do funil num dia específico."""
    dia_iso = dia.isoformat()
    total = 0
    for i in range(6):
        params = {
            "$top": 500,
            "$skip": i * 500,
            "$filter": f"destinationStage eq '{destino}'",
            "$orderby": "createdAt desc",
        }
        resp = cliente._get("/leadStages", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        parou = False
        for s in itens:
            d = (s.get("createdAt") or "")[:10]
            if d < dia_iso:
                parou = True
                break
            if d == dia_iso:
                total += 1
        if parou or len(itens) < 500:
            break
    return total


def _contar_leads_novos(cliente: ExactClient, dia: date) -> int:
    dia_iso = dia.isoformat()
    total = 0
    for i in range(6):
        params = {"$top": 500, "$skip": i * 500, "$orderby": "registerDate desc"}
        resp = cliente._get("/Leads", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        parou = False
        for l in itens:
            d = (l.get("registerDate") or "")[:10]
            if d < dia_iso:
                parou = True
                break
            if d == dia_iso:
                total += 1
        if parou or len(itens) < 500:
            break
    return total


def _vendas_do_mes(cliente: ExactClient) -> int:
    """Vendas fechadas no mês corrente, pela DATA REAL do fechamento.

    Usa /leadStages: o updateDate do lead é a última mexida no cadastro e faz
    venda antiga aparecer no mês errado.
    """
    inicio_mes = date.today().replace(day=1).isoformat()
    ids = set()
    for i in range(10):
        params = {
            "$top": 500,
            "$skip": i * 500,
            "$filter": f"destinationStage eq '{STAGE_GANHO}'",
            "$orderby": "createdAt desc",
        }
        resp = cliente._get("/leadStages", params=params)
        itens = resp.get("value", resp) if isinstance(resp, dict) else resp
        if not itens:
            break
        parou = False
        for s_ in itens:
            d = (s_.get("createdAt") or "")[:10]
            if d < inicio_mes:
                parou = True
                break
            ids.add(s_.get("leadId"))
        if parou or len(itens) < 500:
            break
    return len(ids)


def renderizar(data_inicio: date, data_fim: date, ttl_minutos: int, usar_cache: bool):
    hoje = date.today()

    st.markdown("### 📈 Resumo de hoje")
    st.caption(hoje.strftime("%d/%m/%Y"))

    chave = f"visao_geral_v1:{hoje}"
    dados = cache.buscar(chave, ttl_segundos=min(ttl_minutos, 15) * 60) if usar_cache else None

    if dados is None:
        dados = {}
        # --- Exact ---
        try:
            ex = ExactClient()
            with st.spinner("Carregando indicadores do dia..."):
                dados["leads_hoje"] = _contar_leads_novos(ex, hoje)
                dados["propostas_hoje"] = _contar_leadstages(ex, STAGE_PROPOSTA, hoje)
                dados["ganhos_hoje"] = _contar_leadstages(ex, STAGE_GANHO, hoje)
                dados["vendas_mes"] = _vendas_do_mes(ex)
        except ExactError as e:
            dados["erro_exact"] = str(e)

        # --- 3C Plus ---
        try:
            c3 = TresCPlusClient()
            inicio = f"{hoje.isoformat()} 00:00:00"
            fim = f"{hoje.isoformat()} 23:59:59"
            total_lig = 0
            total_atend = 0
            for _nome, aid in SDRS_3C.items():
                try:
                    stats = c3.agent_statistics(inicio, fim, agent_id=aid)
                    total_atend += sum(d.get("answered") or 0 for d in stats)
                except TresCPlusError:
                    continue
                try:
                    qual = c3.qualification_statistics(inicio, fim, agent_id=aid)
                    for d in qual:
                        for q in (d.get("qualifications") or {}).values():
                            total_lig += q.get("count") or 0
                except TresCPlusError:
                    continue
            dados["ligacoes_hoje"] = total_lig
            dados["atendidas_hoje"] = total_atend
        except TresCPlusError as e:
            dados["erro_3c"] = str(e)

        cache.salvar(chave, dados)

    # =========================================================
    # KPIs do dia
    # =========================================================
    c1, c2, c3_, c4 = st.columns(4)
    c1.metric("Leads novos", dados.get("leads_hoje", "—"))
    c2.metric("Propostas enviadas", dados.get("propostas_hoje", "—"))
    c3_.metric("Vendas fechadas ✅", dados.get("ganhos_hoje", "—"))
    c4.metric(
        "Ligações", dados.get("ligacoes_hoje", "—"),
        help="Chamadas qualificadas pelas operadoras hoje",
    )

    st.divider()

    # =========================================================
    # Mês corrente
    # =========================================================
    st.markdown(f"### 💰 Mês de {hoje.strftime('%m/%Y')}")

    m1, m2 = st.columns(2)
    m1.metric(
        "Vendas fechadas", dados.get("vendas_mes", "—"),
        help="Pela data real do fechamento no funil",
    )
    m2.metric(
        "Contato efetivo hoje", dados.get("atendidas_hoje", "—"),
        help="Chamadas realmente atendidas do outro lado",
    )
    st.caption("O valor de comissão a pagar está na aba Comissões BDR.")

    # --- Avisos de erro, se houver ---
    if dados.get("erro_exact"):
        st.warning(f"Exact indisponível: {dados['erro_exact']}")
    if dados.get("erro_3c"):
        st.warning(f"3C Plus indisponível: {dados['erro_3c']}")

    st.divider()
    st.caption(
        "Os números acima são sempre de **hoje** e do **mês corrente**, "
        "independentes do período escolhido na barra lateral. "
        "Use as outras abas para analisar o período selecionado."
    )
