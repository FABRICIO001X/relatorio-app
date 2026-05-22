# Relatório Unificado — 3C Plus + Exact + SGCor

Dashboard web (Streamlit) que consolida dados das três plataformas.

## Status dos conectores

| Fonte | Status | Como funciona |
|---|---|---|
| **3C Plus** | ✅ API integrada | `https://app.3c.fluxoti.com/v1` com Bearer token |
| **Exact Sales** | ✅ API integrada | `https://api.exactspotter.com/v3` com header `token_exact` (OData) |
| **SGCor** | 🚧 Upload manual | Você exporta CSV/XLSX do SGCor e sobe no app. Quando a API estiver disponível, troca-se só o `connectors/sgcor.py`. |

## Estrutura

```
relatorio_app/
├── app.py                      ← dashboard Streamlit
├── requirements.txt
├── .env.example                ← template de credenciais
├── README.md
├── connectors/
│   ├── tres_c_plus.py          ← cliente REST validado vs SDK oficial
│   ├── exact.py                ← cliente REST validado vs blueprint Apiary
│   └── sgcor.py                ← upload manual + helpers (futuro: API)
└── db/cache.py                 ← cache SQLite com TTL
```

## Como rodar pela primeira vez

```bash
python -m venv .venv
source .venv/bin/activate           # Linux/Mac
# .venv\Scripts\activate             # Windows

pip install -r requirements.txt

cp .env.example .env
# edite o .env com seu token 3C Plus e token Exact

streamlit run app.py
```

Abre em `http://localhost:8501`.

## Onde pegar as credenciais

### 3C Plus
- Token no painel do 3C Plus (módulo de Integrações/API)
- Base URL: `https://app.3c.fluxoti.com` (confirmado via SDK oficial em `github.com/fluxoti/3cplusv2-sdk-js`)

### Exact Sales (Spotter)
- No Spotter: **Configurações > Integrações > "Token Exact API"**
- Base URL: `https://api.exactspotter.com/v3`

### SGCor
- Por enquanto, só exporte os relatórios manualmente e use a aba SGCor do app
- Quando você obtiver a documentação da API com o suporte, me avise — adaptar leva 30 minutos

## Endpoints suportados

### 3C Plus
- `listar_chamadas(data_inicio, data_fim)` — histórico de chamadas
- `chamada(id)` — detalhe de uma chamada
- `chamadas_do_agente()` — chamadas do agente autenticado
- `campanhas_do_agente()` — campanhas do agente
- `baixar_gravacao(ano, mes, dia, arquivo, destino)` — download de áudio

### Exact Sales (15+ endpoints prontos)
- **Leads:** `listar_leads`, `listar_leads_vendidos`, `listar_leads_descartados`, `listar_leads_transferidos`
- **Atividades:** `listar_atividades`, `listar_reunioes`, `historico_ligacoes`, `transcricoes`
- **Pessoas:** `listar_vendedores`, `listar_pre_vendedores`, `listar_grupos`, `listar_organizacoes`
- **Funil:** `listar_funis`, `listar_etapas_funil`, `motivos_descarte`, `origens`
- **Dashboards (KPIs):** `dashboard_desempenho_vendedores`, `dashboard_desempenho_pre_vendedores`,
  `dashboard_metricas_venda`, `dashboard_metricas_pre_venda`, `dashboard_atividades_funil`,
  `dashboard_feedbacks_ligacao_enviados`, `dashboard_feedbacks_ligacao_solicitados`
- **Outros:** `listar_produtos`, `listar_metas`, `listar_bonificacoes`, `cidades`, `mercados`

Todos suportam paginação OData (`$top`, `$skip`, `$filter`).
Helper `listar_todos(path)` pagina automaticamente.

### SGCor (upload manual)
- Você escolhe o tipo de relatório no dropdown
- Sobe o CSV/XLSX exportado do SGCor
- O app guarda em cache e mostra na visão geral

## Cache

- Banco SQLite em `cache.db` (criado automático no primeiro uso)
- TTL configurável pela sidebar (1 min a 4h)
- Botão "Limpar cache" força refetch das APIs

## Segurança

- `.env` **não** vai pro Git
- Credenciais ficam só na máquina que roda o app
- Pra hospedar em servidor, use gerenciador de secrets (Doppler, AWS Secrets Manager, etc.)

## Próximos passos sugeridos

1. **Rodar e testar** com tokens reais
2. **Customizar gráficos** na aba "Visão geral" cruzando dados das 3 fontes
3. **Quando a API do SGCor chegar:**
   - Atualizar `connectors/sgcor.py` mantendo nomes dos métodos
   - Adicionar autenticação no `.env`
   - O `app.py` não precisa de nenhuma mudança
4. **Agendar atualização automática** (cron rodando um script que preenche o cache de madrugada)
