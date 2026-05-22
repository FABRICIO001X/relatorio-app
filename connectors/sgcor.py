"""
Conector do SGCor via automação de navegador (Playwright).

Como SGCor não oferece API pública, fazemos login web e baixamos relatórios.
Para usar, instale o Playwright e os navegadores:

    pip install playwright
    playwright install chromium

Os seletores CSS abaixo são genéricos — você vai precisar abrir o SGCor,
inspecionar (F12) os campos de login e o menu de relatórios, e ajustar
as strings marcadas com TODO.

Quando o SGCor oferecer API REST, substitua esta classe por um cliente
HTTP no mesmo formato (mesma assinatura de métodos) e o app não precisa
ser alterado.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd


class SGCorError(Exception):
    pass


class SGCorClient:
    def __init__(
        self,
        url: str | None = None,
        usuario: str | None = None,
        senha: str | None = None,
        headless: bool = True,
    ):
        self.url = url or os.getenv("SGCOR_URL")
        self.usuario = usuario or os.getenv("SGCOR_USER")
        self.senha = senha or os.getenv("SGCOR_PASSWORD")
        self.headless = headless
        if not all([self.url, self.usuario, self.senha]):
            raise SGCorError("Credenciais do SGCor não configuradas. Edite o .env.")

    def _baixar_relatorio(self, nome_relatorio: str, download_dir: Path) -> Path:
        """Login no SGCor e download de um relatório. Retorna o caminho do CSV/XLSX."""
        from playwright.sync_api import sync_playwright

        download_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            # 1) login
            page.goto(self.url)
            # TODO: ajustar seletores conforme HTML real do SGCor
            page.fill('input[name="usuario"]', self.usuario)
            page.fill('input[name="senha"]', self.senha)
            page.click('button[type="submit"]')
            page.wait_for_load_state("networkidle")

            # 2) navegar até o relatório desejado
            # TODO: substituir pelos cliques reais até a tela do relatório
            page.click(f'text={nome_relatorio}')
            page.wait_for_load_state("networkidle")

            # 3) clicar em exportar e capturar o download
            # TODO: ajustar o seletor do botão de exportar
            with page.expect_download() as download_info:
                page.click('text=Exportar')
            download = download_info.value
            destino = download_dir / download.suggested_filename
            download.save_as(destino)

            browser.close()
            return destino

    # ---------- métodos de negócio ----------
    def listar_apolices(self, download_dir: Path | str = "./downloads") -> pd.DataFrame:
        """Baixa o relatório de apólices e retorna como DataFrame."""
        download_dir = Path(download_dir)
        arquivo = self._baixar_relatorio("Apólices", download_dir)
        if arquivo.suffix.lower() in {".xlsx", ".xls"}:
            return pd.read_excel(arquivo)
        return pd.read_csv(arquivo, sep=None, engine="python", encoding="utf-8-sig")

    def listar_sinistros(self, download_dir: Path | str = "./downloads") -> pd.DataFrame:
        download_dir = Path(download_dir)
        arquivo = self._baixar_relatorio("Sinistros", download_dir)
        if arquivo.suffix.lower() in {".xlsx", ".xls"}:
            return pd.read_excel(arquivo)
        return pd.read_csv(arquivo, sep=None, engine="python", encoding="utf-8-sig")

    # Modo manual: se o Playwright der trabalho no começo, você pode
    # exportar do SGCor manualmente e usar este método para carregar:
    @staticmethod
    def carregar_arquivo(caminho: str | Path) -> pd.DataFrame:
        caminho = Path(caminho)
        if caminho.suffix.lower() in {".xlsx", ".xls"}:
            return pd.read_excel(caminho)
        return pd.read_csv(caminho, sep=None, engine="python", encoding="utf-8-sig")
