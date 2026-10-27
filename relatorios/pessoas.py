"""
Troca de pessoa no mesmo login — quem é quem em cada data.

CASO (Fabricio, 06/10/2026):
A Crislane saiu da equipe e a Melissa entrou no lugar dela em 19/09/2026,
usando os MESMOS logins (Exact 442056 e 3C Plus 200133). Por isso tudo que
a Melissa fazia aparecia nos relatórios com o nome da Crislane.

REGRA:
- Registro com data ANTES de 19/09/2026  → CRISLANE
- Registro com data A PARTIR de 19/09/2026 → MELISSA
Os IDs não mudam; só o nome exibido muda conforme a data do registro.

Cada relatório usa a data que faz sentido para ele:
- SDRs (Exact):      data de cadastro do lead / data real da venda
- 3C Plus:           o dia da ligação
- Comissões BDR:     a data da ação do BDR no lead (quem agiu recebe)
- Meta semanal BDR:  a data em que a visita foi marcada

LOGIN PRÓPRIO (11/10/2026): a Melissa ganhou usuário próprio no Exact (451379,
MELISSA TEIXEIRA) e o da Crislane (442056) foi desativado. Os dois logins
aparecem como MELISSA a partir de 19/09/2026 e os números se somam.

Para adicionar outra troca no futuro, basta incluir mais uma linha em TROCAS.
"""
from __future__ import annotations

from datetime import date, datetime

# login (nome antigo) -> lista de (data_inicio, nome_novo), em ordem crescente
TROCAS: dict[str, list[tuple[date, str]]] = {
    "CRISLANE": [(date(2026, 9, 19), "MELISSA")],
}

# Pessoas que só existem a partir de uma data (login próprio criado depois).
# Antes dessa data elas não aparecem nos relatórios.
INICIO: dict[str, date] = {
    "MELISSA": date(2026, 9, 19),
    "MICAELE": date(2026, 10, 26),  # Micaele Ferreira, Exact 451858
}

# Cores dos novos nomes (os antigos continuam com as cores que cada relatório já usa)
CORES_NOVAS = {
    "MELISSA": "#8E44AD",
    "MICAELE": "#D35400",
}


def _para_date(d) -> date | None:
    """Aceita date, datetime, pandas Timestamp ou string ISO ('2026-09-19T10:00:00')."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        s = str(d)[:10]
        return date.fromisoformat(s)
    except Exception:
        return None


def nome_no_dia(nome_login: str, dia) -> str:
    """Nome de quem estava usando o login `nome_login` no dia `dia`."""
    trocas = TROCAS.get(nome_login)
    if not trocas:
        return nome_login
    d = _para_date(dia)
    if d is None:
        return nome_login
    atual = nome_login
    for inicio, novo in trocas:
        if d >= inicio:
            atual = novo
    return atual


def nomes_no_periodo(nomes_login: list[str], data_inicio: date, data_fim: date) -> list[str]:
    """Lista de nomes a exibir para um período, na ordem dos logins.

    Se o período cruza a data da troca, o login aparece duas vezes
    (ex.: ['IASMIM', 'CRISLANE', 'MELISSA', 'JENNYFER']).
    """
    saida: list[str] = []
    for nome in nomes_login:
        trocas = TROCAS.get(nome)
        if not trocas:
            candidatos = [nome]
        else:
            # nome vigente no início do período
            candidatos = [nome_no_dia(nome, data_inicio)]
            for inicio, novo in trocas:
                if data_inicio < inicio <= data_fim and novo not in candidatos:
                    candidatos.append(novo)
        for c in candidatos:
            ini = INICIO.get(c)
            if ini and data_fim < ini:
                continue  # pessoa ainda não estava na equipe nesse período
            if c not in saida:
                saida.append(c)
    return saida


def cores(cores_base: dict[str, str]) -> dict[str, str]:
    """Mapa de cores do relatório + cores dos nomes novos."""
    return {**cores_base, **CORES_NOVAS}
