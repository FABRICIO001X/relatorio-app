"""
Cliente da API REST do 3C Plus.

Endpoints validados contra o SDK oficial em:
    https://github.com/fluxoti/3cplusv2-sdk-js  (fork público)
    https://github.com/3C-Plus/3cplusv2-sdk     (org oficial)

Autenticação: header `Authorization: Bearer <token>`
Base URL real: https://app.3c.fluxoti.com
Prefixo de versão: /v1

ENDPOINTS DE CHAMADAS (src/v1/call.js):
    GET  /v1/calls                                  -> histórico de chamadas
    GET  /v1/calls/{id}                             -> chamada específica
    GET  /v1/records/{year}/{month}/{day}/{file}    -> baixar gravação

ENDPOINTS DE AGENTE (src/v1/agent.js):
    GET  /v1/agent/calls           -> histórico de chamadas do agente
    POST /v1/agent/login           -> login do agente
    POST /v1/agent/webphone/login  -> login no webphone
    GET  /v1/agent/logout          -> logout
    GET  /v1/agent/connect
    GET  /v1/agent/campaigns       -> campanhas do agente
    POST /v1/qualify               -> qualificar chamada
    POST /v1/hangup                -> desligar
    POST /v1/agent/manual_call/dial
    POST /v1/agent/manual_call/enter
    POST /v1/agent/manual_call/exit

NÃO confundir com endpoints do site institucional 3c.fluxoti.com/api/v1/click2call
(esses são da API de Click2Call e Omnichannel WhatsApp, base diferente).
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import requests


class TresCPlusError(Exception):
    pass


class TresCPlusClient:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        self.token = token or os.getenv("TRES_C_PLUS_TOKEN")
        self.base_url = (
            base_url
            or os.getenv("TRES_C_PLUS_BASE_URL", "https://app.3c.fluxoti.com")
        ).rstrip("/")
        if not self.token or self.token.startswith("cole_"):
            raise TresCPlusError(
                "Token do 3C Plus não configurado. Edite o arquivo .env."
            )

    # ---------- baixo nível ----------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
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
        url = f"{self.base_url}/v1{path}"
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
            raise TresCPlusError(
                f"HTTP {r.status_code} em {method} {path}: {r.text[:200]}"
            ) from e
        except requests.RequestException as e:
            raise TresCPlusError(f"Falha de rede em {path}: {e}") from e

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json)

    # =============================================================
    # CHAMADAS  (src/v1/call.js)
    # =============================================================
    def listar_chamadas(
        self,
        data_inicio: date | None = None,
        data_fim: date | None = None,
        campanha_id: int | None = None,
        page: int = 1,
        per_page: int = 100,
        **filtros_extras: Any,
    ) -> Any:
        """
        Histórico de chamadas da empresa. GET /v1/calls

        Os filtros aceitos não estão documentados no SDK (que repassa qualquer
        dict). Os nomes abaixo seguem a convenção REST mais comum. Se a sua API
        usar nomes diferentes (ex: 'from'/'to', 'date_start'/'date_end'), passe
        via **filtros_extras.
        """
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if data_inicio:
            params["start_date"] = data_inicio.isoformat()
        if data_fim:
            params["end_date"] = data_fim.isoformat()
        if campanha_id is not None:
            params["campaign_id"] = campanha_id
        params.update(filtros_extras)
        return self._get("/calls", params=params)

    def chamada(self, call_id: str | int) -> Any:
        """Chamada específica pelo ID. GET /v1/calls/{id}"""
        return self._get(f"/calls/{call_id}")

    def url_gravacao(self, ano: int, mes: int, dia: int, arquivo: str) -> str:
        """Monta a URL pra baixar uma gravação. Você usa o token no header pra acessar."""
        return f"{self.base_url}/v1/records/{ano}/{mes}/{dia}/{arquivo}"

    def baixar_gravacao(
        self, ano: int, mes: int, dia: int, arquivo: str, destino: str
    ) -> str:
        """Baixa a gravação e salva no caminho 'destino'. Retorna o caminho."""
        url = self.url_gravacao(ano, mes, dia, arquivo)
        try:
            r = requests.get(url, headers=self._headers(), timeout=60, stream=True)
            r.raise_for_status()
            with open(destino, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return destino
        except requests.RequestException as e:
            raise TresCPlusError(f"Falha ao baixar gravação: {e}") from e

    # =============================================================
    # AGENTE  (src/v1/agent.js)
    # =============================================================
    def chamadas_do_agente(self, **filtros: Any) -> Any:
        """Histórico de chamadas do agente autenticado. GET /v1/agent/calls"""
        return self._get("/agent/calls", params=filtros)

    def campanhas_do_agente(self) -> Any:
        """Campanhas do agente. GET /v1/agent/campaigns"""
        return self._get("/agent/campaigns")
