"""
Cliente da API REST do Exact Sales (Spotter).

Endpoints validados contra o blueprint oficial em:
    https://exactspotter.docs.apiary.io/
    https://jsapi.apiary.io/apis/exactspotter.apib

Base URL: https://api.exactspotter.com/v3
Autenticação: header `token_exact: <seu_token>`
    (gerado em Configurações > Integrações > "Token Exact API" no Spotter)
Protocolo: OData v4 — usa $top, $skip, $filter, $select, $orderby, $count
Paginação: padrão de 500 em 500. Use $skip para paginar.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import requests


class ExactError(Exception):
    pass


class ExactClient:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        self.token = token or os.getenv("EXACT_TOKEN")
        self.base_url = (
            base_url
            or os.getenv("EXACT_BASE_URL", "https://api.exactspotter.com/v3")
        ).rstrip("/")
        if not self.token or self.token.startswith("cole_"):
            raise ExactError(
                "Token do Exact não configurado. Edite o arquivo .env."
            )

    # ---------- baixo nível ----------
    def _headers(self) -> dict[str, str]:
        return {
            "token_exact": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(
                method,
                url,
                headers=self._headers(),
                params=params or {},
                json=json,
                timeout=30,
            )
            r.raise_for_status()
            return r.json() if r.content else None
        except requests.HTTPError as e:
            raise ExactError(
                f"HTTP {r.status_code} em {method} {path}: {r.text[:200]}"
            ) from e
        except requests.RequestException as e:
            raise ExactError(f"Falha de rede em {path}: {e}") from e

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json)

    def _odata_params(
        self,
        top: int | None = None,
        skip: int | None = None,
        filter_: str | None = None,
        select: str | None = None,
        orderby: str | None = None,
        count: bool = False,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if top is not None:
            params["$top"] = top
        if skip is not None:
            params["$skip"] = skip
        if filter_:
            params["$filter"] = filter_
        if select:
            params["$select"] = select
        if orderby:
            params["$orderby"] = orderby
        if count:
            params["$count"] = "true"
        if extras:
            params.update(extras)
        return params

    # =============================================================
    # LEADS  (principal entidade do CRM)
    # =============================================================
    def listar_leads(
        self,
        top: int = 100,
        skip: int = 0,
        filter_: str | None = None,
        select: str | None = None,
    ) -> Any:
        """GET /v3/Leads — lista de leads"""
        return self._get(
            "/Leads",
            params=self._odata_params(top=top, skip=skip, filter_=filter_, select=select),
        )

    def listar_leads_descartados(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/Losts — leads descartados (perdidos)"""
        return self._get("/Losts", params=self._odata_params(top=top, skip=skip))

    def listar_leads_transferidos(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/transferHistory — histórico de transferências de lead"""
        return self._get(
            "/transferHistory", params=self._odata_params(top=top, skip=skip)
        )

    def listar_leads_vendidos(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/LeadsSold — leads que viraram venda"""
        return self._get("/LeadsSold", params=self._odata_params(top=top, skip=skip))

    def listar_leads_e_contatos(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/LeadsAndPersons"""
        return self._get(
            "/LeadsAndPersons", params=self._odata_params(top=top, skip=skip)
        )

    # =============================================================
    # ORGANIZAÇÕES / EMPRESAS
    # =============================================================
    def listar_organizacoes(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/organization"""
        return self._get("/organization", params=self._odata_params(top=top, skip=skip))

    # =============================================================
    # ATIVIDADES, REUNIÕES, LIGAÇÕES
    # =============================================================
    def listar_atividades(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/Tasks"""
        return self._get("/Tasks", params=self._odata_params(top=top, skip=skip))

    def listar_tipos_atividade(self) -> Any:
        """GET /v3/TasksType"""
        return self._get("/TasksType")

    def listar_reunioes(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/meetings"""
        return self._get("/meetings", params=self._odata_params(top=top, skip=skip))

    def historico_ligacoes(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/callsHistory — histórico de ligações"""
        return self._get("/callsHistory", params=self._odata_params(top=top, skip=skip))

    def resultados_ligacao(self) -> Any:
        """GET /v3/callsResult"""
        return self._get("/callsResult")

    def historico_qualificacoes(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/QualificationHistories"""
        return self._get(
            "/QualificationHistories", params=self._odata_params(top=top, skip=skip)
        )

    def transcricoes(self, data_inicio: date, data_fim: date) -> Any:
        """
        GET /v3/Transcriptions?initialDate=YYYY-MM-DD&finalDate=YYYY-MM-DD
        Transcrições de chamadas no período.
        """
        return self._get(
            "/Transcriptions",
            params={
                "initialDate": data_inicio.isoformat(),
                "finalDate": data_fim.isoformat(),
            },
        )

    # =============================================================
    # USUÁRIOS, VENDEDORES, PRÉ-VENDEDORES, GRUPOS
    # =============================================================
    def listar_vendedores(self) -> Any:
        """GET /v3/sellers"""
        return self._get("/sellers")

    def listar_pre_vendedores(self) -> Any:
        """GET /v3/sdrs"""
        return self._get("/sdrs")

    def listar_grupos(self) -> Any:
        """GET /v3/Groups"""
        return self._get("/Groups")

    # =============================================================
    # FUNIL E ETAPAS
    # =============================================================
    def listar_funis(self) -> Any:
        """GET /v3/Funnels"""
        return self._get("/Funnels")

    def listar_etapas_funil(self) -> Any:
        """GET /v3/stages"""
        return self._get("/stages")

    # =============================================================
    # PRODUTOS
    # =============================================================
    def listar_produtos(self, top: int = 100, skip: int = 0) -> Any:
        """GET /v3/products"""
        return self._get("/products", params=self._odata_params(top=top, skip=skip))

    def listar_produtos_sugeridos(self) -> Any:
        """GET /v3/recommendedProducts"""
        return self._get("/recommendedProducts")

    # =============================================================
    # MOTIVOS DE DESCARTE, ORIGENS, MERCADO
    # =============================================================
    def motivos_descarte(self) -> Any:
        return self._get("/discardReasons")

    def origens(self) -> Any:
        """GET /v3/sources — origem e sub-origem"""
        return self._get("/sources")

    def mercados(self) -> Any:
        """GET /v3/industries"""
        return self._get("/industries")

    def cidades(self) -> Any:
        """GET /v3/cities"""
        return self._get("/cities")

    # =============================================================
    # DASHBOARDS (KPIs)
    # Todos no formato ?dataInicial=YYYY-MM-DD&dataFinal=YYYY-MM-DD
    # =============================================================
    def _dashboard(
        self, path: str, data_inicio: date, data_fim: date
    ) -> Any:
        return self._get(
            path,
            params={
                "dataInicial": data_inicio.isoformat(),
                "dataFinal": data_fim.isoformat(),
            },
        )

    def dashboard_atividades_funil(self, data_inicio: date, data_fim: date) -> Any:
        return self._dashboard("/FunnelActivity", data_inicio, data_fim)

    def dashboard_desempenho_vendedores(self, data_inicio: date, data_fim: date) -> Any:
        return self._dashboard("/SellerPerformance", data_inicio, data_fim)

    def dashboard_desempenho_pre_vendedores(
        self, data_inicio: date, data_fim: date
    ) -> Any:
        return self._dashboard("/PreSalesPerformance", data_inicio, data_fim)

    def dashboard_metricas_pre_venda(self, data_inicio: date, data_fim: date) -> Any:
        return self._dashboard("/PreSalesMetrics", data_inicio, data_fim)

    def dashboard_metricas_venda(self, data_inicio: date, data_fim: date) -> Any:
        return self._dashboard("/SellersMetrics", data_inicio, data_fim)

    def dashboard_feedbacks_ligacao_enviados(
        self, data_inicio: date, data_fim: date
    ) -> Any:
        return self._dashboard("/CallFeedbacksSent", data_inicio, data_fim)

    def dashboard_feedbacks_ligacao_solicitados(
        self, data_inicio: date, data_fim: date
    ) -> Any:
        return self._dashboard("/CallFeedbackRequests", data_inicio, data_fim)

    # =============================================================
    # METAS, BONIFICAÇÃO
    # =============================================================
    def listar_metas(self) -> Any:
        return self._get("/goals")

    def listar_bonificacoes(self) -> Any:
        return self._get("/bonus")

    # =============================================================
    # PAGINAÇÃO AUTOMÁTICA (helper)
    # =============================================================
    def listar_todos(self, path: str, page_size: int = 500, max_paginas: int = 50) -> list[dict]:
        """
        Pagina automaticamente um endpoint OData até esgotar os resultados.
        Use com cuidado em bases grandes (pode demorar).
        """
        todos: list[dict] = []
        for i in range(max_paginas):
            params = self._odata_params(top=page_size, skip=i * page_size)
            resp = self._get(path, params=params)
            itens = resp.get("value", resp) if isinstance(resp, dict) else resp
            if not itens:
                break
            todos.extend(itens)
            if len(itens) < page_size:
                break
        return todos
