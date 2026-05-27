"""
Conector SGCor — API REST oficial.

Endpoints usados (todos read-only via API oficial):
- POST /login → token JWT
- POST /producao/pesquisar → lista propostas/apólices
- POST /parcelas/nao_recebidas/pesquisar → inadimplência
- POST /parcelas/repasses/pesquisar → comissões a receber
- POST /sinistros/pesquisar → sinistros

Base URL: https://apirest.gruposgcor.com.br/api
Autenticação: email + senha (formdata) → devolve token JWT
"""
from __future__ import annotations

import os
from typing import Any

import requests


class SGCorError(Exception):
    """Erro nas chamadas à API do SGCor."""


class SGCorClient:
    """
    Cliente READ-ONLY pra API do SGCor.

    SEGURANÇA: este conector NUNCA faz POST/PUT/DELETE de dados modificáveis.
    Os endpoints de "pesquisar" usam POST por convenção HTTP mas são consultas.
    """
    BASE_URL = "https://apirest.gruposgcor.com.br/api"

    def __init__(self, email: str | None = None, senha: str | None = None):
        self.email = email or os.getenv("SGCOR_EMAIL", "")
        self.senha = senha or os.getenv("SGCOR_SENHA", "")
        if not self.email or not self.senha:
            raise SGCorError("Credenciais SGCor não configuradas (SGCOR_EMAIL, SGCOR_SENHA)")
        self._token: str | None = None

    def _login(self) -> str:
        """Faz login e retorna o token JWT."""
        try:
            r = requests.post(
                f"{self.BASE_URL}/login",
                data={"email": self.email, "senha": self.senha},
                headers={"Accept": "application/json"},
                timeout=30,
            )
            r.raise_for_status()
            payload = r.json()
            token = payload.get("data", {}).get("token") or payload.get("token")
            if not token:
                raise SGCorError(f"Login OK mas sem token: {payload}")
            return token
        except requests.RequestException as e:
            raise SGCorError(f"Falha no login: {e}") from e

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self._token = self._login()
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _post_pesquisar(
        self,
        path: str,
        body: dict[str, Any],
        max_paginas: int = 50,
    ) -> list[dict]:
        """
        Pagina automaticamente um endpoint de pesquisar.
        Retorna todos os itens encontrados.
        Limite: 50 páginas (10.000 itens com per_page=200).
        """
        todos: list[dict] = []
        url = f"{self.BASE_URL}{path}"

        for page in range(1, max_paginas + 1):
            try:
                r = requests.post(
                    f"{url}?page={page}",
                    json=body,
                    headers=self._headers(),
                    timeout=90,
                )
                if r.status_code == 401:
                    # Token expirou — reautentica e tenta de novo
                    self._token = None
                    r = requests.post(
                        f"{url}?page={page}",
                        json=body,
                        headers=self._headers(),
                        timeout=90,
                    )
                r.raise_for_status()
                payload = r.json()
            except requests.RequestException as e:
                raise SGCorError(f"Erro em {path}: {e}") from e

            itens = payload.get("data", [])
            if not itens:
                break
            todos.extend(itens)

            meta = payload.get("meta", {})
            links = payload.get("links", {})
            if not links.get("next"):
                break
        return todos

    # =========================================================
    # Produção (apólices/propostas)
    # =========================================================
    def producao_pesquisar(
        self,
        tipo_data: str,
        data_inicial: str,
        data_final: str,
    ) -> list[dict]:
        """
        Lista apólices/propostas.
        tipo_data: 'dataEmitida', 'dataVigenciaInicial', 'dataVigenciaFinal', 'dataCancelada'
        Datas no formato 'YYYY-MM-DD'.
        """
        return self._post_pesquisar(
            "/producao/pesquisar",
            {
                "tipoData": tipo_data,
                "dataInicial": data_inicial,
                "dataFinal": data_final,
            },
        )

    # =========================================================
    # Parcelas não recebidas (inadimplência)
    # =========================================================
    def parcelas_nao_recebidas(
        self,
        tipo_data: str,
        data_inicial: str,
        data_final: str,
    ) -> list[dict]:
        """
        tipo_data: 'dataVencimento', 'dataVigenciaInicial', 'dataVigenciaFinal'
        """
        return self._post_pesquisar(
            "/parcelas/nao_recebidas/pesquisar",
            {
                "tipoData": tipo_data,
                "dataInicial": data_inicial,
                "dataFinal": data_final,
            },
        )

    # =========================================================
    # Repasses ao produtor (comissões)
    # =========================================================
    def parcelas_repasses(
        self,
        tipo_data: str,
        data_inicial: str,
        data_final: str,
    ) -> list[dict]:
        """
        tipo_data: 'dataVencimento', 'dataVigenciaInicial', 'dataVigenciaFinal'
        """
        return self._post_pesquisar(
            "/parcelas/repasses/pesquisar",
            {
                "tipoData": tipo_data,
                "dataInicial": data_inicial,
                "dataFinal": data_final,
            },
        )
