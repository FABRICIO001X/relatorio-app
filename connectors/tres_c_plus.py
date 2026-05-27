"""
Conector 3C Plus (Fluxoti) — API REST oficial v1.

Descobertas validadas em produção:
- Base URL: https://app.3c.plus/api/v1
- Auth: query string ?api_token=TOKEN (NÃO header Bearer)
- Datas: formato 'Y-m-d H:i:s' (com horário, NÃO só YYYY-MM-DD)

Endpoints usados (TODOS read-only via GET):
- /agents → lista de operadores
- /calls → histórico de chamadas
- /qualification/statistics → resultados das ligações (atendida, ocupado, etc.)
- /agents/statistics/by_agent → produtividade por operador

⚠️ SEGURANÇA: este conector NUNCA faz POST/PUT/DELETE.
"""
from __future__ import annotations

import os
from typing import Any

import requests


class TresCPlusError(Exception):
    """Erro nas chamadas à API do 3C Plus."""


class TresCPlusClient:
    """Cliente READ-ONLY pra API do 3C Plus."""
    BASE_URL = "https://app.3c.plus/api/v1"

    def __init__(self, api_token: str | None = None):
        self.api_token = api_token or os.getenv("TRES_C_PLUS_TOKEN", "")
        if not self.api_token:
            raise TresCPlusError("Token 3C Plus não configurado (TRES_C_PLUS_TOKEN)")

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict:
        """Faz GET autenticado. Retorna o JSON.

        TRAVA DE SEGURANÇA: este método só aceita GET.
        """
        # api_token sempre vai como query string
        all_params = {"api_token": self.api_token, **(params or {})}
        url = f"{self.BASE_URL}{path}"

        # Tenta até 2 vezes em caso de timeout
        ultimo_erro = None
        for tentativa in range(2):
            try:
                r = requests.get(url, params=all_params, timeout=90)
                if r.status_code >= 500:
                    # Servidor com problema, tenta de novo
                    ultimo_erro = r.text[:200]
                    continue
                if r.status_code >= 400:
                    raise TresCPlusError(
                        f"HTTP {r.status_code} em {path}: {r.text[:300]}"
                    )
                return r.json() if r.content else {}
            except requests.exceptions.Timeout as e:
                ultimo_erro = str(e)
                continue
            except requests.RequestException as e:
                raise TresCPlusError(f"Falha de rede em {path}: {e}") from e
        raise TresCPlusError(f"Timeout em {path} após 2 tentativas: {ultimo_erro}")

    def _get_paginado(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_paginas: int = 50,
        per_page: int = 200,
    ) -> list[dict]:
        """Pagina automaticamente endpoints que devolvem meta.pagination."""
        todos: list[dict] = []
        params = dict(params or {})
        params["per_page"] = per_page
        for page in range(1, max_paginas + 1):
            params["page"] = page
            payload = self._get(path, params=params)
            itens = payload.get("data", [])
            if not itens:
                break
            todos.extend(itens)
            # Olhar paginação
            pag = (payload.get("meta") or {}).get("pagination") or {}
            total_pages = pag.get("total_pages")
            if total_pages and page >= total_pages:
                break
            if len(itens) < per_page:
                break
        return todos

    # =========================================================
    # Agentes
    # =========================================================
    def listar_agentes(self) -> list[dict]:
        """Lista todos os agentes do 3C Plus."""
        payload = self._get("/agents")
        return payload.get("data") or payload or []

    # =========================================================
    # Chamadas
    # =========================================================
    def listar_chamadas(
        self,
        start_date: str,
        end_date: str,
        max_paginas: int = 30,
    ) -> list[dict]:
        """
        Lista chamadas entre start_date e end_date.
        Formato esperado: 'Y-m-d H:i:s' (ex: '2026-05-01 00:00:00').
        """
        return self._get_paginado(
            "/calls",
            params={"start_date": start_date, "end_date": end_date},
            max_paginas=max_paginas,
        )

    # =========================================================
    # Qualificações (resultados das ligações)
    # =========================================================
    def qualification_statistics(
        self,
        start_date: str,
        end_date: str,
        agent_id: int | None = None,
    ) -> list[dict]:
        """
        Estatísticas de qualificação (resultado das ligações) por dia.
        Retorna lista de {date, qualifications: {...}}.
        """
        params = {"start_date": start_date, "end_date": end_date}
        if agent_id:
            params["agent_id"] = agent_id
        payload = self._get("/qualification/statistics", params=params)
        return payload.get("data") or []
