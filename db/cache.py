"""Cache simples em SQLite para evitar bater nas APIs toda hora."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pandas as pd

DB_PATH = Path("cache.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cache (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL,
            atualizado_em REAL NOT NULL
        )"""
    )
    return conn


def salvar(chave: str, dados: Any) -> None:
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO cache (chave, valor, atualizado_em) VALUES (?, ?, ?)",
        (chave, json.dumps(dados, default=str), time.time()),
    )
    conn.commit()
    conn.close()


def buscar(chave: str, ttl_segundos: int = 3600) -> Any | None:
    """Retorna o valor se existir e estiver dentro do TTL, senão None."""
    conn = _conn()
    row = conn.execute(
        "SELECT valor, atualizado_em FROM cache WHERE chave = ?", (chave,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    valor, atualizado_em = row
    if (time.time() - atualizado_em) > ttl_segundos:
        return None
    return json.loads(valor)


def salvar_df(chave: str, df: pd.DataFrame) -> None:
    salvar(chave, df.to_dict(orient="records"))


def buscar_df(chave: str, ttl_segundos: int = 3600) -> pd.DataFrame | None:
    dados = buscar(chave, ttl_segundos)
    if dados is None:
        return None
    return pd.DataFrame(dados)


def limpar() -> None:
    conn = _conn()
    conn.execute("DELETE FROM cache")
    conn.commit()
    conn.close()
