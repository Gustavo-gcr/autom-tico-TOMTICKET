import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import unicodedata

# =====================================================================
# CONFIGURAÇÃO
# =====================================================================
st.set_page_config(layout="wide", page_title="Relatório TomTicket - Sistemas UAU")

DEPARTAMENTO = "02 - Sistemas UAU"
COLECAO_RESUMOS = "resumos"            # 1 documento por mês (ex.: "012025")
ANOS_CONGELADOS = [2024, 2025]         # só resumo: gerado 1x e nunca mais lê os chamados brutos
ANOS_HISTORICO = [2024, 2025, 2026]    # anos que entram nas abas de comparativo/evolução
ANOS_SELECT = [2024, 2025, 2026]       # anos no seletor da barra lateral (2026 = ano "vivo")

HORAS_VALIDADE_ULTIMO_MES = 6          # último mês enviado: resumo refeito no máx. a cada 6h
LIMITE_LEITURAS_DIA = 30000            # trava de segurança do app (cota gratuita = 50.000)
LIMITE_LEITURAS_POR_RESUMO = 20000     # máximo de chamados lidos para montar 1 mês
FILTRAR_DEPARTAMENTO_NO_SERVIDOR = True  # só lê (e cobra) docs do departamento

MESES_DICT = {'Janeiro': 1, 'Fevereiro': 2, 'Março': 3, 'Abril': 4, 'Maio': 5, 'Junho': 6,
              'Julho': 7, 'Agosto': 8, 'Setembro': 9, 'Outubro': 10, 'Novembro': 11, 'Dezembro': 12}
MESES_ABREV = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

# Totais fixos (meses não preenchidos no banco). Chave = "MMAAAA".
TOTAIS_FIXOS = {"052026": 547}

# ---- Segundo banco (plataforma de obras) — SOMENTE LEITURA ----
CATEGORIA_OBRA = "Criação de Obra"     # entra como mais uma categoria, mas conta OBRAS criadas (não chamados)
COLECAO_OBRAS = "obras"                # <-- CONFIRMAR: nome da coleção das obras no banco obras-68dbe
CAMPO_CRIADOR_OBRA = "criadoPor"       # campo da obra que diz quem criou (confirmado na amostra do banco)
CAMPOS_DATA_OBRA = ["criadoEm", "createdAt", "dataCriacao", "criado_em", "data", "timestamp"]  # <-- CONFIRMAR o campo de data
TIPOS_OBRA_CONTADOS = ["Obra Nova"]    # None = conta qualquer tipo
# e-mail cadastrado na plataforma de obras -> nome do atendente no TomTicket
ATENDENTES_OBRAS = {
    "crislane.oliveira@lcmconstrucao.com.br": "Crislane Oliveira",
    "crislane.oliveira@lcmcostrucao.com.br": "Crislane Oliveira",   # grafia como veio na mensagem
    "israel.santos@lcmconstrucao.com.br": "Israel Santos",
    "tiago@lcmconstrucao.com.br": "Tiago",
    "angelo.silva@lcmconstrucao.com.br": "Angelo Silva",
}

lcm_light_green = '#78B94B'
lcm_gray = '#9E9E9E'


# =====================================================================
# GRÁFICOS
# =====================================================================
def mostrar(fig, container=st):
    if fig is not None:
        container.pyplot(fig)
        plt.close(fig)


def plot_standard_bar(data_series, x_label, y_label, title=None, color=lcm_light_green):
    if data_series.empty:
        st.warning("Sem dados para gerar gráfico.")
        return None

    fig, ax = plt.subplots(figsize=(14, 6))
    bars = ax.bar(data_series.index, data_series.values, color=color,
                  edgecolor='black', linewidth=1.2, zorder=3)
    ax.set_ylabel(y_label, fontsize=10)
    ax.set_title(title if title else "", fontsize=12, fontweight='bold')
    plt.xticks(rotation=90, ha='center', fontsize=9)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    for bar in bars:
        height = bar.get_height()
        ax.annotate(f'{int(height)}', xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3), textcoords="offset points",
                    ha='center', va='bottom', fontsize=9, fontweight='bold')
    plt.tight_layout()
    return fig


def plot_comparison_bar(df_grouped, title):
    """Barras agrupadas por mês, uma barra por ano (colunas = anos)."""
    if df_grouped.empty:
        return None

    meses = df_grouped.index
    anos = list(df_grouped.columns)
    x = np.arange(len(meses))
    width = 0.8 / max(len(anos), 1)
    cores = {2024: '#D5D5D5', 2025: lcm_gray, 2026: lcm_light_green}
    fig, ax = plt.subplots(figsize=(14, 7))

    for i, ano in enumerate(anos):
        offset = (i - (len(anos) - 1) / 2) * width
        rects = ax.bar(x + offset, df_grouped[ano].values, width, label=str(ano),
                       color=cores.get(ano, '#4D8337'), edgecolor='black', linewidth=1)
        for rect in rects:
            h = rect.get_height()
            if h > 0:
                ax.annotate(f'{int(h)}', xy=(rect.get_x() + rect.get_width() / 2, h),
                            xytext=(0, 3), textcoords="offset points",
                            ha='center', va='bottom', fontsize=8)

    ax.set_ylabel('Quantidade')
    ax.set_title(title, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(meses)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend()
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    plt.tight_layout()
    return fig


def plot_evolution_line_bar(df_grouped, title):
    """Barras empilhadas por categoria, com total no topo. Índice = nomes dos meses."""
    if df_grouped.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 7))
    df_grouped = df_grouped.reindex(MESES_ABREV, fill_value=0)
    x = np.arange(len(MESES_ABREV))
    colors = [lcm_light_green, '#4D8337', '#2E521F', '#A3D681', lcm_gray, '#B4E59C']
    bottom_y = np.zeros(len(MESES_ABREV))

    for i, col in enumerate(df_grouped.columns):
        cor = colors[i % len(colors)]
        vals = df_grouped[col].values
        bars = ax.bar(x, vals, bottom=bottom_y, label=col, color=cor, edgecolor='black', linewidth=1)
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.annotate(f'{int(h)}', (bar.get_x() + bar.get_width() / 2, bar.get_y() + h / 2),
                            ha='center', va='center',
                            color='white' if i % 2 != 0 else 'black',
                            fontsize=9, fontweight='bold')
        bottom_y += vals

    for i, total in enumerate(bottom_y):
        if total > 0:
            ax.annotate(f'{int(total)}', (x[i], total), xytext=(0, 5), textcoords="offset points",
                        ha='center', va='bottom', fontsize=10, fontweight='bold', color='black')

    ax.set_title(title, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(MESES_ABREV)
    ax.set_ylim(bottom=0)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    handles, _ = ax.get_legend_handles_labels()
    if handles:
        ax.legend(title="Categorias", bbox_to_anchor=(1.0, 1.0))
    plt.tight_layout()
    return fig


# =====================================================================
# FIREBASE
# =====================================================================
def _validar_chave(nome_secret, key_dict):
    """Falha com mensagem clara se a private_key dos Secrets estiver incompleta ou com placeholder."""
    pk = str(key_dict.get("private_key", ""))
    if (not pk.startswith("-----BEGIN PRIVATE KEY-----") or len(pk) < 1000
            or "..." in pk or "COLE" in pk.upper()):
        raise ValueError(
            f"A private_key de [{nome_secret}] nos Secrets está incompleta ou com texto de exemplo ('...'). "
            "Copie o valor completo do campo private_key do arquivo JSON da conta de serviço."
        )


@st.cache_resource
def init_firestore():
    if not firebase_admin._apps:
        try:
            if "FIREBASE_CREDENTIALS" in st.secrets:
                key_dict = dict(st.secrets["FIREBASE_CREDENTIALS"])
                if "private_key" in key_dict:
                    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
                _validar_chave("FIREBASE_CREDENTIALS", key_dict)
                firebase_admin.initialize_app(credentials.Certificate(key_dict))
            else:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                json_path = os.path.join(current_dir, "serviceAccountKey.json")
                if os.path.exists(json_path):
                    firebase_admin.initialize_app(credentials.Certificate(json_path))
                else:
                    st.error("Nenhuma credencial encontrada (Secrets ou JSON).")
                    return None
        except Exception as e:
            st.error(f"Erro na conexão com Firebase: {e}")
            return None
    return firestore.client()


db = init_firestore()
if not db:
    st.stop()


@st.cache_resource
def init_firestore_obras():
    """Conexão com o banco de OBRAS. Use uma conta de serviço com papel 'Leitor do Cloud Datastore'
    (roles/datastore.viewer): assim o banco só aceita leitura, mesmo que o código tente escrever."""
    try:
        if "FIREBASE_OBRAS_CREDENTIALS" in st.secrets:
            key_dict = dict(st.secrets["FIREBASE_OBRAS_CREDENTIALS"])
            if "private_key" in key_dict:
                key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
            _validar_chave("FIREBASE_OBRAS_CREDENTIALS", key_dict)
            cred = credentials.Certificate(key_dict)
        else:
            json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serviceAccountObras.json")
            if not os.path.exists(json_path):
                return None
            cred = credentials.Certificate(json_path)
        try:
            app = firebase_admin.get_app("obras")
        except ValueError:
            app = firebase_admin.initialize_app(cred, name="obras")
        return firestore.client(app=app)
    except Exception as e:
        st.sidebar.warning(f"Banco de obras indisponível: {e}")
        return None


db_obras = init_firestore_obras()


# =====================================================================
# CONTADOR DE LEITURAS (trava de segurança, por processo do app)
# =====================================================================
@st.cache_resource
def _contador():
    return {"dia": None, "leituras": 0}


def leituras_hoje():
    c = _contador()
    hoje = datetime.now(ZoneInfo("America/Los_Angeles")).date()  # cota do Firestore zera à meia-noite (Pacífico)
    if c["dia"] != hoje:
        c["dia"], c["leituras"] = hoje, 0
    return c


def registrar_leituras(n):
    leituras_hoje()["leituras"] += int(n)


# =====================================================================
# RESUMOS MENSAIS  (coleção "resumos", 1 documento por mês)
# =====================================================================
def _chave(v):
    v = '' if v is None else str(v).strip()
    if v.lower() in ('', 'nan', 'none'):
        v = 'Não Informado'
    if v.startswith('__') and v.endswith('__'):
        v = v.strip('_') or 'Não Informado'
    return v[:200]


def _contar(series):
    return {str(k): int(v) for k, v in series.value_counts().items()}


def construir_resumo(df):
    """Transforma os chamados brutos do mês em contagens agregadas."""
    df = df.copy()
    for col in ['Categoria', 'Atendente', 'Atendente Criador', 'Última Situação']:
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].fillna('').astype(str).str.strip()

    tmp = pd.DataFrame({
        'cat': df['Categoria'].map(_chave),
        'att': df['Atendente'].map(_chave),
        'sit': df['Última Situação'].map(_chave),
    })

    criador_norm = df['Atendente Criador'].str.lower()
    atendente_norm = df['Atendente'].str.lower()
    mask_self = (criador_norm == atendente_norm) & ~atendente_norm.isin(['', 'nan', 'none', 'não informado'])
    auto = tmp[mask_self.values]

    return {
        "total": int(len(df)),
        "por_categoria": _contar(tmp['cat']),
        "por_atendente": _contar(tmp['att']),
        "por_situacao": _contar(tmp['sit']),
        "por_atendente_categoria": {a: _contar(g['cat']) for a, g in tmp.groupby('att')},
        "auto_por_atendente": _contar(auto['att']),
        "auto_atendente_categoria": {a: _contar(g['cat']) for a, g in auto.groupby('att')},
    }


def _ref_mes(colecao):
    ref = db.collection(colecao)
    if FILTRAR_DEPARTAMENTO_NO_SERVIDOR:
        ref = ref.where(filter=FieldFilter("dados.Departamento", "==", DEPARTAMENTO))
    return ref


@st.cache_data(ttl=600, show_spinner=False)
def contar_docs_servidor(ano, mes):
    """Conta os chamados do mês SEM baixá-los (cobra ~1 leitura a cada 1.000 docs)."""
    try:
        res = _ref_mes(f"{mes:02d}{ano}").count().get()
        n = int(res[0][0].value)
        registrar_leituras(max(1, -(-n // 1000)))
        return n
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def ultimo_mes_com_dados():
    """Último mês (ano, mes) que já tem chamados no banco. Custo: no máx. ~1 leitura por mês testado."""
    hoje = datetime.now()
    y, m = hoje.year, hoje.month
    inicio = (min(ANOS_SELECT), 1)
    while (y, m) >= inicio:
        try:
            docs = list(_ref_mes(f"{m:02d}{y}").limit(1).stream())
        except Exception:
            docs = []
        registrar_leituras(max(1, len(docs)))
        if docs:
            return (y, m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return None


def ler_mes_bruto(colecao, max_docs):
    """Lê os chamados de um mês (única função que consome leituras 'pesadas')."""
    docs = list(_ref_mes(colecao).limit(max_docs + 1).stream())
    registrar_leituras(len(docs))
    if len(docs) > max_docs:
        raise RuntimeError(
            f"A coleção {colecao} excede o limite de leituras permitido nesta execução ({max_docs}). "
            "Aumente LIMITE_LEITURAS_POR_RESUMO/LIMITE_LEITURAS_DIA ou tente após o reset da cota."
        )
    lista = []
    for doc in docs:
        d = doc.to_dict()
        dados = d.get('dados', d)
        dados['id_chamado'] = doc.id
        lista.append(dados)
    df = pd.DataFrame(lista)
    if not df.empty and 'Departamento' in df.columns:
        df = df[df['Departamento'] == DEPARTAMENTO]
    return df, len(docs)


def _limpar_caches_resumo():
    carregar_todos_resumos.clear()
    contar_docs_servidor.clear()
    ultimo_mes_com_dados.clear()


def gerar_resumo(ano, mes):
    """Lê o mês UMA vez e grava o documento de resumo."""
    orcamento = min(LIMITE_LEITURAS_POR_RESUMO, LIMITE_LEITURAS_DIA - leituras_hoje()["leituras"])
    if orcamento <= 0:
        raise RuntimeError("Limite diário de leituras do app atingido. Tente novamente após o reset da cota.")
    doc_id = f"{mes:02d}{ano}"
    doc_ref = db.collection(COLECAO_RESUMOS).document(doc_id)
    df, n_lidos = ler_mes_bruto(doc_id, orcamento)

    # Mês "vivo" sem chamados: não deixa resumo vazio/velho para trás (ex.: mês ainda não enviado)
    if df.empty and ano not in ANOS_CONGELADOS:
        doc_ref.delete()
        _limpar_caches_resumo()
        return 0

    resumo = construir_resumo(df)
    resumo.update({"ano": int(ano), "mes": int(mes), "total_banco": int(n_lidos),
                   "gerado_em": firestore.SERVER_TIMESTAMP})
    doc_ref.set(resumo)  # sem merge: substitui o resumo antigo
    _limpar_caches_resumo()
    return resumo["total"]


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_todos_resumos():
    """Lê TODOS os resumos (~36 leituras no máximo). Alimenta mês selecionado + abas de histórico."""
    docs = list(db.collection(COLECAO_RESUMOS).stream())
    registrar_leituras(len(docs))
    return {d.id: d.to_dict() for d in docs}


def aplicar_totais_fixos(todos):
    """Força o total dos meses em TOTAIS_FIXOS (cria um resumo mínimo se o mês não existir no banco)."""
    todos = dict(todos)
    for doc_id, total in TOTAIS_FIXOS.items():
        r = dict(todos.get(doc_id) or {})
        r.update({"ano": int(doc_id[2:]), "mes": int(doc_id[:2]), "total": total})
        todos[doc_id] = r
    return todos


# =====================================================================
# OBRAS CRIADAS NA PLATAFORMA (segundo banco — apenas consultas, nenhuma escrita)
# =====================================================================
def _norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode()
    return " ".join(txt.lower().split())


def _parse_data(v):
    """Aceita Timestamp do Firestore, epoch (s/ms), ISO ou dd/mm/aaaa. Retorna datetime em America/Sao_Paulo."""
    sp = ZoneInfo("America/Sao_Paulo")
    dt = None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, (int, float)):
        dt = datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
    elif isinstance(v, str):
        t = v.strip()
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            for f in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
                try:
                    dt = datetime.strptime(t, f)
                    break
                except ValueError:
                    pass
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=sp)
    return dt.astimezone(sp)


@st.cache_data(ttl=1800, show_spinner=False)
def carregar_obras():
    """Lê (somente leitura) as obras dos atendentes listados e conta por (ano, mês, atendente).
    Retorna ({(ano, mes): {nome: qtd}}, info_diagnostico)."""
    info = {"lidos": 0, "contados": 0, "sem_data": 0, "ignorados_tipo": 0, "campo_data": None, "erro": None}
    if db_obras is None:
        info["erro"] = "Banco de obras não configurado (credenciais ausentes)."
        return {}, info
    emails = list(ATENDENTES_OBRAS)
    contagem = {}
    try:
        ref = db_obras.collection(COLECAO_OBRAS)
        for i in range(0, len(emails), 30):  # limite de 30 valores no filtro "in"
            for doc in ref.where(filter=FieldFilter(CAMPO_CRIADOR_OBRA, "in", emails[i:i + 30])).stream():
                info["lidos"] += 1
                d = doc.to_dict() or {}
                if TIPOS_OBRA_CONTADOS and d.get("tipo") not in TIPOS_OBRA_CONTADOS:
                    info["ignorados_tipo"] += 1
                    continue
                campo = next((c for c in CAMPOS_DATA_OBRA if d.get(c) is not None), None)
                dt = _parse_data(d.get(campo)) if campo else None
                if dt is None:
                    info["sem_data"] += 1
                    continue
                info["campo_data"] = campo
                nome = ATENDENTES_OBRAS.get(str(d.get(CAMPO_CRIADOR_OBRA, "")).strip().lower()) or d.get(CAMPO_CRIADOR_OBRA)
                por_mes = contagem.setdefault((dt.year, dt.month), {})
                por_mes[nome] = por_mes.get(nome, 0) + 1
                info["contados"] += 1
    except Exception as e:
        info["erro"] = str(e)
    return contagem, info


def _casar_nome(nome, existentes):
    """Casa o nome vindo das obras com o nome do atendente já existente no resumo do TomTicket."""
    n = _norm(nome)
    for e in existentes:
        if _norm(e) == n:
            return e
    toks = set(n.split())
    cand = [e for e in existentes if toks and (toks <= set(_norm(e).split()) or set(_norm(e).split()) <= toks)]
    return cand[0] if len(cand) == 1 else nome


def aplicar_obras(todos):
    """Soma as obras criadas à categoria 'Criação de Obra' (e ao total) de cada mês. Só em memória."""
    contagem, _ = carregar_obras()
    if not contagem:
        return todos
    todos = dict(todos)
    for doc_id, r in list(todos.items()):
        por_att = contagem.get((r.get("ano"), r.get("mes")))
        if not por_att:
            continue
        r = dict(r)
        total_obras = sum(por_att.values())
        r["total"] = r.get("total", 0) + total_obras
        r["total_obras"] = total_obras
        if r.get("por_categoria"):   # mês com detalhamento: entra como categoria/atendente
            existentes = set(r.get("por_atendente", {}))
            por_cat = dict(r["por_categoria"])
            por_cat[CATEGORIA_OBRA] = por_cat.get(CATEGORIA_OBRA, 0) + total_obras
            por_a = dict(r.get("por_atendente", {}))
            por_ac = {a: dict(c) for a, c in r.get("por_atendente_categoria", {}).items()}
            for nome, q in por_att.items():
                alvo = _casar_nome(nome, existentes)
                por_a[alvo] = por_a.get(alvo, 0) + q
                por_ac.setdefault(alvo, {})
                por_ac[alvo][CATEGORIA_OBRA] = por_ac[alvo].get(CATEGORIA_OBRA, 0) + q
            r.update({"por_categoria": por_cat, "por_atendente": por_a, "por_atendente_categoria": por_ac})
        todos[doc_id] = r
    return todos


def carregar_painel():
    return aplicar_obras(aplicar_totais_fixos(carregar_todos_resumos()))


def precisa_reconstruir(resumo, ano, mes, ultimo):
    """Decide se é preciso reler os chamados do mês para refazer o resumo."""
    # Meses com total fixo no código não são relidos do banco.
    if f"{mes:02d}{ano}" in TOTAIS_FIXOS:
        return False

    # Anos arquivados (2024/2025): gera 1x e pronto.
    if ano in ANOS_CONGELADOS:
        return resumo is None

    # Ano "vivo" (2026): mês que ainda não foi enviado ao banco não é lido.
    if resumo is None:
        return bool(ultimo) and (ano, mes) <= ultimo

    # Já existe resumo: só relê se a quantidade de chamados no banco mudou (contagem custa ~1 leitura)...
    n = contar_docs_servidor(ano, mes)
    if n is not None and n != resumo.get("total_banco"):
        return True

    # ...ou se é o último mês enviado e o resumo está velho (situações podem mudar sem mudar a contagem).
    if ultimo and (ano, mes) == ultimo:
        gerado = resumo.get("gerado_em")
        if not isinstance(gerado, datetime):
            return True
        idade_h = (datetime.now(timezone.utc) - gerado).total_seconds() / 3600
        return idade_h > HORAS_VALIDADE_ULTIMO_MES
    return False


# =====================================================================
# POP-UP DE ATUALIZAÇÃO VIA XLS/CSV
# =====================================================================
@st.dialog("Atualizar Atendente Criador")
def show_update_dialog():
    st.write("Selecione o período e a planilha para preencher o campo **Atendente Criador** no Firebase.")
    st.info("O sistema irá cruzar os dados pelo número do Protocolo.")

    col1, col2 = st.columns(2)
    mes_up = col1.selectbox("Mês de Referência", list(MESES_DICT.keys()))
    ano_up = col2.selectbox("Ano de Referência", ANOS_SELECT, index=len(ANOS_SELECT) - 1)

    target_collection = f"{MESES_DICT[mes_up]:02d}{ano_up}"
    st.markdown(f"**Coleção Alvo:** `{target_collection}`")

    refazer = st.checkbox("Refazer o resumo do mês após atualizar (lê o mês uma vez)", value=True)
    uploaded_file = st.file_uploader("Carregar planilha", type=["xlsx", "xls", "csv"])

    if st.button("Processar Atualização", type="primary"):
        if not uploaded_file:
            st.error("Por favor, envie um arquivo válido.")
            return

        try:
            filename = uploaded_file.name.lower()

            # 1. Leitura bruta
            if filename.endswith('.csv'):
                try:
                    df_raw = pd.read_csv(uploaded_file, header=None)
                except UnicodeDecodeError:
                    uploaded_file.seek(0)
                    df_raw = pd.read_csv(uploaded_file, header=None, encoding='latin1', sep=';')
            else:
                df_raw = pd.read_excel(uploaded_file, header=None)

            # 2. Cabeçalho
            header_row_idx = None
            for idx, row in df_raw.head(30).iterrows():
                if any("Protocolo" in s for s in row.astype(str).values):
                    header_row_idx = idx
                    break

            uploaded_file.seek(0)

            # 3. Recarrega com cabeçalho correto
            if filename.endswith('.csv'):
                try:
                    df = pd.read_csv(uploaded_file, skiprows=header_row_idx) if header_row_idx is not None \
                        else pd.read_csv(uploaded_file)
                except Exception:
                    uploaded_file.seek(0)
                    df = pd.read_csv(uploaded_file, skiprows=header_row_idx, encoding='latin1', sep=';') \
                        if header_row_idx is not None else pd.read_csv(uploaded_file, encoding='latin1', sep=';')
            else:
                df = pd.read_excel(uploaded_file, skiprows=header_row_idx) if header_row_idx is not None \
                    else pd.read_excel(uploaded_file)

            # 4. Validação
            cols_necessarias = ['Protocolo', 'Atendente Criador']
            if not all(col in df.columns for col in cols_necessarias):
                st.error(f"O arquivo deve conter as colunas: {', '.join(cols_necessarias)}")
                return

            # 5. Firebase (somente escritas)
            progress_bar = st.progress(0.0)
            status_text = st.empty()
            batch = db.batch()
            count_batch = count_total = 0
            BATCH_LIMIT = 400
            total_rows = max(len(df), 1)

            for i, (_, row) in enumerate(df.iterrows()):
                protocolo = str(row['Protocolo']).strip()
                criador = str(row['Atendente Criador']).strip()
                if not protocolo or protocolo.lower() == 'nan' or not criador or criador.lower() == 'nan':
                    continue

                doc_ref = db.collection(target_collection).document(protocolo)
                batch.set(doc_ref, {"dados": {"Atendente Criador": criador}}, merge=True)
                count_batch += 1
                count_total += 1

                if count_batch >= BATCH_LIMIT:
                    batch.commit()
                    batch = db.batch()
                    count_batch = 0
                if i % 10 == 0:
                    progress_bar.progress(min(i / total_rows, 1.0))
                    status_text.text(f"Processando protocolo {protocolo}...")

            if count_batch > 0:
                batch.commit()

            progress_bar.progress(1.0)
            status_text.text("Concluído!")
            st.success(f"✅ Atualização finalizada! {count_total} chamados processados.")

            if refazer:
                with st.spinner("Refazendo o resumo do mês..."):
                    total = gerar_resumo(int(ano_up), MESES_DICT[mes_up])
                st.success(f"Resumo de {mes_up}/{ano_up} atualizado ({total} chamados).")
            st.cache_data.clear()

        except Exception as e:
            st.error(f"Erro ao processar: {e}")


# =====================================================================
# BARRA LATERAL
# =====================================================================
st.sidebar.header("Filtros")
st.sidebar.markdown(f"**Departamento Fixo:** {DEPARTAMENTO}")

agora = datetime.now()
ultimo = ultimo_mes_com_dados()                       # (ano, mes) do último mês enviado ao banco
ref_ano, ref_mes = ultimo if ultimo else (agora.year, agora.month)

mes_selecionado = st.sidebar.selectbox("Mês", list(MESES_DICT.keys()), index=ref_mes - 1, key="sel_mes")
ano_idx = ANOS_SELECT.index(ref_ano) if ref_ano in ANOS_SELECT else len(ANOS_SELECT) - 1
ano_selecionado = st.sidebar.selectbox("Ano", ANOS_SELECT, index=ano_idx, key="sel_ano")
mes_num = MESES_DICT[mes_selecionado]
doc_id_sel = f"{mes_num:02d}{ano_selecionado}"
if ultimo:
    st.sidebar.caption(f"Último mês enviado ao banco: **{MESES_ABREV[ref_mes - 1]}/{ref_ano}**")

todos = carregar_painel()

# ---- Configurações (escondidas num popover, para o usuário comum não clicar sem querer) ----
st.sidebar.markdown("---")
_config = st.sidebar.popover("⚙️ Configurações", use_container_width=True) \
    if hasattr(st.sidebar, "popover") else st.sidebar.expander("⚙️ Configurações")

with _config:
    if st.button("📂 Atualizar Base (XLS)", use_container_width=True):
        show_update_dialog()

    if st.button("🔄 Recalcular resumo deste mês", use_container_width=True):
        try:
            with st.spinner("Lendo o mês e gerando o resumo..."):
                gerar_resumo(ano_selecionado, mes_num)
            st.rerun()
        except Exception as e:
            st.error(str(e))

    with st.expander("🛠️ Administração dos resumos"):
        c = leituras_hoje()
        st.caption(f"Leituras feitas pelo app hoje: **{c['leituras']:,}** / {LIMITE_LEITURAS_DIA:,}")

        pendentes = []
        for ano in ANOS_HISTORICO:
            for m in range(1, 13):
                esperado = ano in ANOS_CONGELADOS or (bool(ultimo) and (ano, m) <= ultimo)
                if esperado and f"{m:02d}{ano}" not in todos:
                    pendentes.append((ano, m))
        st.write(f"Meses sem resumo: **{len(pendentes)}**")
        if pendentes:
            st.caption(", ".join(f"{m:02d}/{a}" for a, m in pendentes))
            if st.button("Gerar resumos faltantes"):
                barra = st.progress(0.0)
                for n, (a, m) in enumerate(pendentes, 1):
                    try:
                        total = gerar_resumo(a, m)
                        st.write(f"✅ {m:02d}/{a}: {total} chamados")
                    except Exception as e:
                        st.error(f"Parou em {m:02d}/{a}: {e}")
                        break
                    barra.progress(n / len(pendentes))
                st.info("Recarregue a página para ver o resultado.")

        if todos:
            linhas_adm = []
            for k, v in sorted(todos.items(), key=lambda kv: (kv[1].get('ano', 0), kv[1].get('mes', 0))):
                g = v.get('gerado_em')
                linhas_adm.append({"Mês": f"{k[:2]}/{k[2:]}", "Total": v.get('total', 0),
                                   "Gerado em": g.astimezone().strftime('%d/%m %H:%M') if isinstance(g, datetime) else "-"})
            st.dataframe(pd.DataFrame(linhas_adm), hide_index=True, height=250)

    with st.expander("🏗️ Diagnóstico – Obras (somente leitura)"):
        if db_obras is None:
            st.warning("Banco de obras não conectado. Configure FIREBASE_OBRAS_CREDENTIALS nos Secrets "
                       "ou coloque serviceAccountObras.json ao lado do app.")
        else:
            if st.button("🔁 Recarregar obras"):
                carregar_obras.clear()
                st.rerun()
            _, info_o = carregar_obras()
            st.json(info_o)
            if st.button("Listar coleções e ver amostra"):
                try:
                    st.write("Coleções:", [c.id for c in db_obras.collections()])
                    amostra = list(db_obras.collection(COLECAO_OBRAS).limit(1).stream())
                    if amostra:
                        st.write({k: type(v).__name__ for k, v in amostra[0].to_dict().items()})
                        _a = amostra[0].to_dict()
                        st.write("Valores da amostra:", {k: _a.get(k) for k in (CAMPO_CRIADOR_OBRA, "tipo", "criadoEm")})
                        _vals = sorted({str(x.to_dict().get(CAMPO_CRIADOR_OBRA)) for x in
                                        db_obras.collection(COLECAO_OBRAS).limit(50).stream()})
                        st.write(f"Valores de '{CAMPO_CRIADOR_OBRA}' em até 50 obras:", _vals)
                    else:
                        st.info(f"Coleção '{COLECAO_OBRAS}' vazia ou inexistente.")
                except Exception as e:
                    st.error(str(e))

# ---- Garante o resumo do mês selecionado ----
resumo = todos.get(doc_id_sel)
if precisa_reconstruir(resumo, ano_selecionado, mes_num, ultimo):
    try:
        with st.spinner(f"Atualizando resumo de {mes_selecionado}/{ano_selecionado}..."):
            gerar_resumo(ano_selecionado, mes_num)
        todos = carregar_painel()
        resumo = todos.get(doc_id_sel)
    except Exception as e:
        st.warning(f"Não foi possível atualizar o resumo agora: {e}")

# =====================================================================
# PÁGINA
# =====================================================================
st.title(f'Relatório Mensal TomTicket - {mes_selecionado}/{ano_selecionado}')
if resumo:
    gerado = resumo.get("gerado_em")
    legenda = f"Total de chamados: **{resumo.get('total', 0)}**"
    if resumo.get("total_obras"):
        legenda += f" (inclui **{resumo['total_obras']}** obras criadas na plataforma)"
    if isinstance(gerado, datetime):
        legenda += f" • resumo gerado em {gerado.astimezone().strftime('%d/%m/%Y %H:%M')}"
    st.caption(legenda)

tabs = st.tabs([
    "Análise por Categoria", "Análise por Atendente", "Painel do Atendente",
    "Detalhamento Painel", "Categorias por Atendente", "Situação", "Treinamento",
    "Comparativo Mensal", "Evolução Categorias (Anual)"
])


def serie(d):
    if not d:
        return pd.Series(dtype='int64')
    return pd.Series(d, dtype='int64').sort_values(ascending=False)


def tabela_e_grafico(s, x_label, y_label, titulo=None):
    c1, c2 = st.columns([1, 2])
    c1.dataframe(s.rename("Chamados").to_frame(), use_container_width=True)
    mostrar(plot_standard_bar(s, x_label, y_label, titulo), c2)


tem_dados = bool(resumo) and resumo.get('total', 0) > 0 and bool(resumo.get('por_categoria'))

# ---------- Abas do mês (usam apenas o resumo: 0 leituras extras) ----------
if not tem_dados:
    for i in range(7):
        with tabs[i]:
            st.warning(f"Sem dados detalhados para o período {mes_selecionado}/{ano_selecionado}.")
else:
    por_att_cat = resumo.get('por_atendente_categoria', {})

    with tabs[0]:
        tabela_e_grafico(serie(resumo.get('por_categoria')), 'Categoria', 'Contagem')

    with tabs[1]:
        tabela_e_grafico(serie(resumo.get('por_atendente')), 'Atendente', 'Contagem')

    with tabs[2]:
        st.markdown("### Chamados onde o Atendente Criou para Si Mesmo")
        s = serie(resumo.get('auto_por_atendente'))
        if not s.empty:
            tabela_e_grafico(s, 'Atendente', 'Qtd Auto-Chamados (Criado pelo próprio)')
        else:
            st.info("Nenhum chamado encontrado onde o criador é o próprio atendente.")

    with tabs[3]:
        st.subheader("O que cada Atendente criou para si mesmo?")
        auto_cat = resumo.get('auto_atendente_categoria', {})
        if auto_cat:
            sel = st.selectbox("Selecione o Atendente:", sorted(auto_cat.keys()))
            s = serie(auto_cat.get(sel))
            tabela_e_grafico(s, 'Categoria', f'Categorias criadas por {sel} (Auto-Chamado)')
        else:
            st.info("Sem dados de auto-chamados para detalhar.")

    with tabs[4]:
        st.subheader("Visão Geral: Categorias atendidas")
        if por_att_cat:
            sel = st.selectbox("Selecione o Atendente", sorted(por_att_cat.keys()), key='sel_cat_att')
            tabela_e_grafico(serie(por_att_cat.get(sel)), 'Categoria', f'Total atendido por {sel}')

    with tabs[5]:
        tabela_e_grafico(serie(resumo.get('por_situacao')), 'Situação', 'Contagem')

    with tabs[6]:
        st.subheader("Chamados de Treinamento")
        treino = {a: cats['Treinamento'] for a, cats in por_att_cat.items() if 'Treinamento' in cats}
        if treino:
            tabela_e_grafico(serie(treino), 'Atendente', 'Qtd Treinamentos Realizados')
        else:
            st.info("Nenhum chamado com a categoria 'Treinamento' encontrado neste mês.")

# ---------- Comparativo mensal (somente resumos) ----------
with tabs[7]:
    st.subheader('Comparativo Global por Ano')
    anos_disp = sorted({r.get('ano') for r in todos.values()
                        if r.get('ano') in ANOS_HISTORICO and r.get('total', 0) > 0})
    if anos_disp:
        padrao = [a for a in (2025, 2026) if a in anos_disp] or anos_disp
        anos_sel = st.multiselect("Anos", anos_disp, default=padrao)
        linhas = [(r['ano'], r['mes'], r.get('total', 0)) for r in todos.values() if r.get('ano') in anos_sel]
        if linhas:
            df_tot = pd.DataFrame(linhas, columns=['Ano', 'Mes', 'Total'])
            df_grouped = (df_tot.pivot_table(index='Mes', columns='Ano', values='Total',
                                             aggfunc='sum', fill_value=0)
                          .reindex(index=range(1, 13), columns=sorted(anos_sel), fill_value=0))
            df_grouped.index = MESES_ABREV
            mostrar(plot_comparison_bar(df_grouped, 'Comparativo de Chamados Totais'))
        else:
            st.info("Selecione pelo menos um ano.")
    else:
        st.info("Ainda não há resumos gerados. Use ⚙️ Configurações > Administração dos resumos na barra lateral.")

# ---------- Evolução por categoria (somente resumos) ----------
with tabs[8]:
    st.subheader('Evolução Anual por Categoria Específica')
    cats_disponiveis = sorted({cat for r in todos.values() if r.get('ano') in ANOS_HISTORICO
                               for cat in r.get('por_categoria', {}).keys()})
    if cats_disponiveis:
        c_sel1, c_sel2 = st.columns(2)
        sel_ano_evo = c_sel1.selectbox("Selecione o Ano", ANOS_HISTORICO, index=len(ANOS_HISTORICO) - 1)
        sel_cats_evo = c_sel2.multiselect("Selecione as Categorias", cats_disponiveis,
                                          default=[cats_disponiveis[0]])
        if sel_cats_evo:
            linhas_evo = {}
            for m in range(1, 13):
                r = todos.get(f"{m:02d}{sel_ano_evo}")
                linhas_evo[MESES_ABREV[m - 1]] = r.get('por_categoria', {}) if r else {}
            df_evo = (pd.DataFrame.from_dict(linhas_evo, orient='index')
                      .reindex(columns=sel_cats_evo).fillna(0).astype(int)
                      .reindex(MESES_ABREV, fill_value=0))
            if df_evo.values.sum() > 0:
                mostrar(plot_evolution_line_bar(df_evo, f"Evolução em {sel_ano_evo}"))
                with st.expander("Ver dados brutos"):
                    st.dataframe(df_evo)
            else:
                st.warning("Nenhum dado encontrado para estas categorias neste ano.")
        else:
            st.info("Selecione pelo menos uma categoria.")
    else:
        st.info("Histórico indisponível. Gere os resumos em ⚙️ Configurações > Administração dos resumos.")