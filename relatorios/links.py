"""
Coluna de link para a ficha do lead no Exact Spotter.

Uso nos relatorios:

    from relatorios.links import adicionar_coluna_link, config_link

    tem_link = adicionar_coluna_link(df_v)          # cria a coluna a partir de lead_id
    st.dataframe(df_v, column_config=config_link())  # renderiza como link clicavel
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from connectors.exact import url_lead

COLUNA_LINK = "Abrir no Exact"


def adicionar_coluna_link(df: pd.DataFrame, coluna_id: str = "lead_id") -> bool:
    """
    Cria a coluna de link a partir do id do lead.

    Retorna False quando o DataFrame nao tem a coluna de id (por exemplo,
    um cache antigo gravado antes dessa mudanca) ou quando nenhum id e valido.
    """
    if coluna_id not in df.columns:
        return False
    df[COLUNA_LINK] = df[coluna_id].map(url_lead)
    return bool(df[COLUNA_LINK].notna().any())


def config_link() -> dict:
    """column_config do st.dataframe para a coluna de link."""
    return {
        COLUNA_LINK: st.column_config.LinkColumn(
            "Exact",
            display_text="abrir ↗",
            help="Abre a ficha do lead no Exact Spotter",
        )
    }
