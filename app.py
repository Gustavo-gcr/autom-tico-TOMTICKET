import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import os

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(layout="wide", page_title="Relatório TomTicket - Sistemas UAU")

# Cores LCM
lcm_light_green = '#78B94B' 
lcm_gray = '#9E9E9E'

# --- FUNÇÃO DE GRÁFICO PADRÃO (BARRAS SIMPLES) ---
def plot_standard_bar(data_series, x_label, y_label, title=None, color=lcm_light_green):
    if data_series.empty:
        st.warning("Sem dados para gerar gráfico.")
        return None
    
    fig, ax = plt.subplots(figsize=(14, 6))
    bars = ax.bar(data_series.index, data_series.values, color=color, edgecolor='black', linewidth=1.2, zorder=3)
    
    ax.set_ylabel(y_label, fontsize=10)
    ax.set_title(title if title else "", fontsize=12, fontweight='bold')
    plt.xticks(rotation=90, ha='center', fontsize=9)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    
    # Rótulos de dados
    for bar in bars:
        height = bar.get_height()
        ax.annotate(f'{int(height)}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=9, fontweight='bold')
    
    plt.tight_layout()
    return fig

# --- FUNÇÃO PARA GRÁFICO COMPARATIVO MENSAL ---
def plot_comparison_bar(df_grouped, title):
    if df_grouped.empty: return None

    meses = df_grouped.index
    x = np.arange(len(meses))
    width = 0.40

    fig, ax = plt.subplots(figsize=(14, 7))

    # 2025
    vals_24 = df_grouped.get(2025, pd.Series(0, index=meses))
    rects1 = ax.bar(x - width/2, vals_24, width, label='2025', color=lcm_gray, edgecolor='black', linewidth=1)

    # 2026
    vals_25 = df_grouped.get(2026, pd.Series(0, index=meses))
    rects2 = ax.bar(x + width/2, vals_25, width, label='2026', color=lcm_light_green, edgecolor='black', linewidth=1)
    ax.set_ylabel('Quantidade')
    ax.set_title(title, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(meses)
    
    # Correção do erro da legenda: Só plota se houver dados
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend()
        
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    def autolabel(rects):
        for rect in rects:
            height = rect.get_height()
            if height > 0:
                ax.annotate(f'{int(height)}',
                            xy=(rect.get_x() + rect.get_width() / 2, height),
                            xytext=(0, 3), textcoords="offset points",
                            ha='center', va='bottom', fontsize=8)

    autolabel(rects1)
    autolabel(rects2)

    plt.tight_layout()
    return fig

# --- FUNÇÃO GRÁFICO EVOLUÇÃO CATEGORIAS (ATUALIZADA) ---
def plot_evolution_line_bar(df_grouped, title):
    """Plota a evolução mensal das categorias selecionadas (Empilhadas com Totais)."""
    if df_grouped.empty: return None

    fig, ax = plt.subplots(figsize=(14, 7))
    
    # Prepara eixo X
    meses_ordem = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    df_grouped = df_grouped.reindex(meses_ordem, fill_value=0)
    
    x = np.arange(len(meses_ordem))
    
    # Paleta de verdes
    colors = [lcm_light_green, '#4D8337', '#2E521F', '#A3D681', lcm_gray, '#B4E59C']
    
    bottom_y = np.zeros(len(meses_ordem))
    
    # Loop para criar as barras empilhadas
    for i, col in enumerate(df_grouped.columns):
        cor = colors[i % len(colors)]
        vals = df_grouped[col].values
        
        # Plota a barra da categoria atual
        bars = ax.bar(x, vals, bottom=bottom_y, label=col, color=cor, edgecolor='black', linewidth=1)
        
        # Adiciona o valor DENTRO da barra
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                cy = bar.get_y() + height / 2
                ax.annotate(f'{int(height)}', 
                            (bar.get_x() + bar.get_width()/2, cy), 
                            ha='center', va='center', 
                            color='white' if i % 2 != 0 else 'black',
                            fontsize=9, fontweight='bold')
        
        bottom_y += vals

    # --- Adiciona o TOTAL no topo ---
    for i, total in enumerate(bottom_y):
        if total > 0:
            ax.annotate(f'{int(total)}', 
                        (x[i], total), 
                        xytext=(0, 5), textcoords="offset points", 
                        ha='center', va='bottom', 
                        fontsize=10, fontweight='bold', color='black')

    # Configurações Finais
    ax.set_title(title, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(meses_ordem)
    ax.set_ylim(bottom=0)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    
    # CORREÇÃO DO ERRO DE LEGENDA AQUI
    # Verifica se existem itens rotulados antes de chamar legend()
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(title="Categorias", bbox_to_anchor=(1.0, 1.0))
    
    plt.tight_layout()
    return fig

# --- CONEXÃO COM FIREBASE ---
@st.cache_resource
def init_firestore():
    if not firebase_admin._apps:
        try:
            # Tenta carregar do Streamlit Secrets
            if "FIREBASE_CREDENTIALS" in st.secrets:
                # Converte para dicionário normal para podermos editar
                key_dict = dict(st.secrets["FIREBASE_CREDENTIALS"])
                
                # CORREÇÃO CRÍTICA:
                # Garante que a chave privada tenha as quebras de linha corretas
                if "private_key" in key_dict:
                    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
                
                cred = credentials.Certificate(key_dict)
                firebase_admin.initialize_app(cred)
            
            # Fallback para arquivo local (para seus testes no PC)
            else:
                import os
                current_dir = os.path.dirname(os.path.abspath(__file__))
                json_path = os.path.join(current_dir, "serviceAccountKey.json")
                if os.path.exists(json_path):
                    cred = credentials.Certificate(json_path)
                    firebase_admin.initialize_app(cred)
                else:
                    st.error("Nenhuma credencial encontrada (Secrets ou JSON).")
                    return None
                    
        except Exception as e:
            st.error(f"Erro na conexão com Firebase: {e}")
            return None
            
    return firestore.client()
db = init_firestore()
if not db: st.stop()

# --- POP-UP DE ATUALIZAÇÃO VIA XLS/CSV (CORRIGIDO PARA .document()) ---
@st.dialog("Atualizar Atendente Criador")
def show_update_dialog():
    st.write("Selecione o período e a planilha para preencher o campo **Atendente Criador** no Firebase.")
    st.info("O sistema irá cruzar os dados pelo número do Protocolo.")

    meses_dict_up = {'Janeiro':'01', 'Fevereiro':'02', 'Março':'03', 'Abril':'04', 'Maio':'05', 'Junho':'06',
                     'Julho':'07', 'Agosto':'08', 'Setembro':'09', 'Outubro':'10', 'Novembro':'11', 'Dezembro':'12'}
    
    col1, col2 = st.columns(2)
    mes_up = col1.selectbox("Mês de Referência", list(meses_dict_up.keys()))
    ano_up = col2.selectbox("Ano de Referência", ['2024', '2025', '2026'])
    
    target_collection = f"{meses_dict_up[mes_up]}{ano_up}"
    st.markdown(f"**Coleção Alvo:** `{target_collection}`")

    # Atualizado para aceitar csv, xls e xlsx
    uploaded_file = st.file_uploader("Carregar planilha", type=["xlsx", "xls", "csv"])
    
    if st.button("Processar Atualização", type="primary"):
        if not uploaded_file:
            st.error("Por favor, envie um arquivo válido.")
            return
        
        try:
            filename = uploaded_file.name.lower()
            df_raw = pd.DataFrame()

            # --- 1. LEITURA BRUTA DO ARQUIVO ---
            if filename.endswith('.csv'):
                try:
                    df_raw = pd.read_csv(uploaded_file, header=None)
                except UnicodeDecodeError:
                    # Se falhar UTF-8, tenta Latin-1
                    uploaded_file.seek(0)
                    df_raw = pd.read_csv(uploaded_file, header=None, encoding='latin1', sep=';')
            else:
                # Se for Excel (.xlsx ou .xls) usa read_excel
                df_raw = pd.read_excel(uploaded_file, header=None)

            # --- 2. IDENTIFICAÇÃO DO CABEÇALHO ---
            header_row_idx = None
            
            # Varre as primeiras 30 linhas para achar onde está escrito "Protocolo"
            for idx, row in df_raw.head(30).iterrows():
                row_str = row.astype(str).values
                if any("Protocolo" in s for s in row_str):
                    header_row_idx = idx
                    break
            
            uploaded_file.seek(0)
            
            # --- 3. RECARREGAMENTO COM O CABEÇALHO CORRETO ---
            if filename.endswith('.csv'):
                try:
                    if header_row_idx is not None:
                        df = pd.read_csv(uploaded_file, skiprows=header_row_idx)
                    else:
                        df = pd.read_csv(uploaded_file)
                except:
                    # Fallback de encoding novamente se necessário
                    uploaded_file.seek(0)
                    if header_row_idx is not None:
                        df = pd.read_csv(uploaded_file, skiprows=header_row_idx, encoding='latin1', sep=';')
                    else:
                        df = pd.read_csv(uploaded_file, encoding='latin1', sep=';')
            else:
                # Recarrega Excel pulando as linhas inúteis
                if header_row_idx is not None:
                    df = pd.read_excel(uploaded_file, skiprows=header_row_idx)
                else:
                    df = pd.read_excel(uploaded_file)

            # --- 4. VALIDAÇÃO DAS COLUNAS ---
            cols_necessarias = ['Protocolo', 'Atendente Criador']
            if not all(col in df.columns for col in cols_necessarias):
                st.error(f"O arquivo deve conter as colunas: {', '.join(cols_necessarias)}")
                return

            # --- 5. PROCESSAMENTO FIREBASE ---
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            batch = db.batch()
            count_batch = 0
            count_total = 0
            BATCH_LIMIT = 400
            
            total_rows = len(df)
            
            for index, row in df.iterrows():
                protocolo = str(row['Protocolo']).strip()
                criador = str(row['Atendente Criador']).strip()
                
                # Pula se não tiver protocolo ou criador válido
                if not protocolo or protocolo.lower() == 'nan' or not criador or criador.lower() == 'nan':
                    continue
                
                # CORREÇÃO AQUI: Trocado .doc() por .document()
                doc_ref = db.collection(target_collection).document(protocolo)
                
                # Atualiza apenas o campo específico
                batch.set(doc_ref, {"dados": {"Atendente Criador": criador}}, merge=True)
                
                count_batch += 1
                count_total += 1
                
                if count_batch >= BATCH_LIMIT:
                    batch.commit()
                    batch = db.batch()
                    count_batch = 0
                    
                if index % 10 == 0:
                    prog = min(index / total_rows, 1.0)
                    progress_bar.progress(prog)
                    status_text.text(f"Processando protocolo {protocolo}...")
            
            # Commita o restante
            if count_batch > 0:
                batch.commit()
            
            progress_bar.progress(100)
            status_text.text("Concluído!")
            st.success(f"✅ Atualização finalizada! {count_total} chamados processados.")
            st.cache_data.clear() # Limpa cache para refletir as mudanças
            
        except Exception as e:
            st.error(f"Erro ao processar: {e}")

# --- FUNÇÕES DE DADOS ---
def get_month_data(collection_id):
    try:
        docs = db.collection(collection_id).stream()
        lista = []
        for doc in docs:
            d = doc.to_dict()
            dados = d.get('dados', d)
            dados['id_chamado'] = doc.id
            lista.append(dados)
        return pd.DataFrame(lista)
    except:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_historical_data():
    all_data = []
    anos = ['2025', '2026']
    meses_nums = [f"{i:02d}" for i in range(1, 13)]
    
    for ano in anos:
        for mes in meses_nums:
            col_name = f"{mes}{ano}"
            try:
                df_temp = get_month_data(col_name)
                if not df_temp.empty:
                    if 'Departamento' not in df_temp.columns: df_temp['Departamento'] = 'Não Informado'
                    df_temp = df_temp[df_temp['Departamento'] == "02 - Sistemas UAU"]
                    if not df_temp.empty:
                        df_temp['Mês_Ref'] = int(mes)
                        df_temp['Ano_Ref'] = int(ano)
                        all_data.append(df_temp)
            except:
                continue
    
    if all_data:
        full_df = pd.concat(all_data, ignore_index=True)
        cols_text = ['Atendente', 'Atendente Criador', 'Categoria', 'Última Situação']
        for col in cols_text:
            if col in full_df.columns:
                full_df[col] = full_df[col].astype(str).str.strip()
        return full_df
    return pd.DataFrame()

# --- BARRA LATERAL ---
st.sidebar.header("Filtros")
st.sidebar.markdown("**Departamento Fixo:** 02 - Sistemas UAU")

meses_dict = {'Janeiro':'01', 'Fevereiro':'02', 'Março':'03', 'Abril':'04', 'Maio':'05', 'Junho':'06',
              'Julho':'07', 'Agosto':'08', 'Setembro':'09', 'Outubro':'10', 'Novembro':'11', 'Dezembro':'12'}

mes_atual_idx = datetime.now().month - 1
mes_selecionado = st.sidebar.selectbox("Mês", list(meses_dict.keys()), index=mes_atual_idx)
ano_selecionado = st.sidebar.selectbox("Ano", ['2024', '2025', '2026'], index=1)

# --- NOVO BOTÃO DE ATUALIZAÇÃO ---
st.sidebar.markdown("---")
if st.sidebar.button("📂 Atualizar Base (XLS)"):
    show_update_dialog()
st.sidebar.markdown("---")

collection_name = f"{meses_dict[mes_selecionado]}{ano_selecionado}"

# --- CARREGAMENTO DO MÊS SELECIONADO ---
worksheet_df = get_month_data(collection_name)

if not worksheet_df.empty:
    if 'Departamento' not in worksheet_df.columns: worksheet_df['Departamento'] = 'Não Informado'
    worksheet_df = worksheet_df[worksheet_df['Departamento'] == "02 - Sistemas UAU"]

    cols_check = ['Categoria', 'Atendente', 'Origem do Chamado', 'Atendente Criador', 'Última Situação']
    for col in cols_check:
        if col not in worksheet_df.columns:
            worksheet_df[col] = 'Não Informado'
        else:
            # Limpeza básica
            worksheet_df[col] = worksheet_df[col].astype(str).str.strip()

    # --- LÓGICA DE NORMALIZAÇÃO PARA COMPARAR ATENDENTE X CRIADOR ---
    # Cria colunas temporárias em minúsculo para comparação robusta
    worksheet_df['criador_norm'] = worksheet_df['Atendente Criador'].str.lower()
    worksheet_df['atendente_norm'] = worksheet_df['Atendente'].str.lower()

historical_df = get_historical_data()

st.title(f'Relatório Mensal TomTicket - {mes_selecionado}/{ano_selecionado}')

# --- ABAS ---
tabs_list = [
    "Análise por Categoria", 
    "Análise por Atendente", 
    "Painel do Atendente", 
    "Detalhamento Painel", 
    "Categorias por Atendente", 
    "Situação",
    "Treinamento",
    "Comparativo Mensal",
    "Evolução Categorias (Anual)"
]

tabs = st.tabs(tabs_list)

if worksheet_df.empty:
    st.warning(f"Sem dados encontrados para o período {mes_selecionado}/{ano_selecionado}.")
else:
    # 1. CATEGORIA
    with tabs[0]:
        counts = worksheet_df['Categoria'].value_counts()
        c1, c2 = st.columns([1, 2])
        c1.write(counts)
        fig = plot_standard_bar(counts, 'Categoria', 'Contagem')
        if fig: c2.pyplot(fig)

    # 2. ATENDENTE
    with tabs[1]:
        counts = worksheet_df['Atendente'].value_counts()
        c1, c2 = st.columns([1, 2])
        c1.write(counts)
        fig = plot_standard_bar(counts, 'Atendente', 'Contagem')
        if fig: c2.pyplot(fig)

    # 3. PAINEL DO ATENDENTE (CORRIGIDO)
    with tabs[2]:
        st.markdown("### Chamados onde o Atendente Criou para Si Mesmo")
        
        # Filtro Robusto: compara as colunas normalizadas (minúsculas)
        # Ignora 'nan', 'none' ou 'não informado' para evitar falso positivo
        mask_self = (
            (worksheet_df['criador_norm'] == worksheet_df['atendente_norm']) & 
            (worksheet_df['atendente_norm'] != 'nan') &
            (worksheet_df['atendente_norm'] != 'não informado') &
            (worksheet_df['atendente_norm'] != 'none')
        )
        
        df_self = worksheet_df[mask_self]
        
        if not df_self.empty:
            counts = df_self['Atendente'].value_counts()
            c1, c2 = st.columns([1, 2])
            c1.write(counts)
            fig = plot_standard_bar(counts, 'Atendente', 'Qtd Auto-Chamados (Criado pelo próprio)')
            c2.pyplot(fig)
        else:
            st.info("Nenhum chamado encontrado onde o criador é o próprio atendente.")

    # 4. DETALHAMENTO PAINEL (CORRIGIDO)
    with tabs[3]:
        st.subheader("O que cada Atendente criou para si mesmo?")
        
        # Reaproveita a máscara robusta criada na aba anterior
        mask_self = (
            (worksheet_df['criador_norm'] == worksheet_df['atendente_norm']) & 
            (worksheet_df['atendente_norm'] != 'nan') & 
            (worksheet_df['atendente_norm'] != 'não informado')
        )
        df_self = worksheet_df[mask_self]
        
        opts = df_self['Atendente'].unique()
        
        if len(opts) > 0:
            sel_att = st.selectbox("Selecione o Atendente:", opts)
            # Filtra pelo atendente selecionado
            df_att = df_self[df_self['Atendente'] == sel_att]
            
            if not df_att.empty:
                cat_counts = df_att['Categoria'].value_counts()
                c1, c2 = st.columns([1, 2])
                c1.write(cat_counts)
                fig = plot_standard_bar(cat_counts, 'Categoria', f'Categorias criadas por {sel_att} (Auto-Chamado)')
                c2.pyplot(fig)
            else:
                st.warning("Sem dados para este atendente.")
        else:
            st.info("Sem dados de auto-chamados para detalhar.")

    # 5. CATEGORIAS POR ATENDENTE (GERAL)
    with tabs[4]:
        st.subheader("Visão Geral: Categorias atendidas")
        opts = worksheet_df['Atendente'].dropna().unique()
        if len(opts) > 0:
            sel_att_g = st.selectbox("Selecione o Atendente", opts, key='sel_cat_att')
            df_att_g = worksheet_df[worksheet_df['Atendente'] == sel_att_g]
            counts = df_att_g['Categoria'].value_counts()
            
            c1, c2 = st.columns([1, 2])
            c1.write(counts)
            fig = plot_standard_bar(counts, 'Categoria', f'Total atendido por {sel_att_g}')
            c2.pyplot(fig)

    # 6. SITUAÇÃO
    with tabs[5]:
        counts = worksheet_df['Última Situação'].value_counts()
        c1, c2 = st.columns([1, 2])
        c1.write(counts)
        fig = plot_standard_bar(counts, 'Situação', 'Contagem')
        if fig: c2.pyplot(fig)

    # 7. TREINAMENTO
    with tabs[6]:
        st.subheader("Chamados de Treinamento")
        df_treino = worksheet_df[worksheet_df['Categoria'] == 'Treinamento']
        
        if not df_treino.empty:
            counts = df_treino['Atendente'].value_counts()
            c1, c2 = st.columns([1, 2])
            c1.write(counts)
            
            fig = plot_standard_bar(counts, 'Atendente', 'Qtd Treinamentos Realizados')
            c2.pyplot(fig)
        else:
            st.info("Nenhum chamado com a categoria 'Treinamento' encontrado neste mês.")

# 8. COMPARATIVO MENSAL
with tabs[7]:
    st.subheader('Comparativo Global (2025 vs 2026)')
    if not historical_df.empty:
        df_grouped = historical_df.groupby(['Ano_Ref', 'Mês_Ref']).size().unstack(level=0, fill_value=0)
        all_months = pd.Index(range(1, 13), name='Mês_Ref')
        df_grouped = df_grouped.reindex(all_months, fill_value=0)
        meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
        df_grouped.index = meses_nomes
        
        fig_comp = plot_comparison_bar(df_grouped, 'Comparativo de Chamados Totais')
        st.pyplot(fig_comp)
    else:
        st.info("Carregando histórico...")

# 9. EVOLUÇÃO CATEGORIAS
with tabs[8]:
    st.subheader('Evolução Anual por Categoria Especifica')
    
    if not historical_df.empty:
        cats_disponiveis = sorted(historical_df['Categoria'].unique())
        c_sel1, c_sel2 = st.columns(2)
        
        sel_ano_evo = c_sel1.selectbox("Selecione o Ano", [2025, 2026], index=1)
        sel_cats_evo = c_sel2.multiselect("Selecione as Categorias", cats_disponiveis, default=[cats_disponiveis[0]] if cats_disponiveis else None)
        
        if sel_cats_evo:
            df_hist_filt = historical_df[
                (historical_df['Ano_Ref'] == sel_ano_evo) & 
                (historical_df['Categoria'].isin(sel_cats_evo))
            ]
            
            if not df_hist_filt.empty:
                df_evo = df_hist_filt.groupby(['Mês_Ref', 'Categoria']).size().unstack(fill_value=0)
                fig_evo = plot_evolution_line_bar(df_evo, f"Evolução em {sel_ano_evo}")
                st.pyplot(fig_evo)
                
                with st.expander("Ver dados brutos"):
                    st.dataframe(df_evo)
            else:
                st.warning("Nenhum dado encontrado para estas categorias neste ano.")
        else:
            st.info("Selecione pelo menos uma categoria.")
    else:
        st.info("Histórico indisponível.")