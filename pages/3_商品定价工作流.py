import streamlit as st
import pandas as pd
import numpy as np
import re
import math
import unicodedata
import io
from rapidfuzz import fuzz
from tqdm import tqdm
from collections import defaultdict

st.set_page_config(page_title="商品定价工作流", layout="wide")
st.title("💰 商品定价工作流（匹配 → 分类 → 定价）")
st.markdown("依次执行6个步骤，可下载中间结果，也支持单独步骤上传文件运行。")

# ----------------------------- 全局辅助函数 ---------------------------------
def fullwidth_to_halfwidth(text):
    if not isinstance(text, str):
        return text
    return unicodedata.normalize('NFKC', text)

def normalize_text(text):
    if pd.isna(text):
        return ""
    text = str(text)
    text = fullwidth_to_halfwidth(text)
    text = re.sub(r'[【\[\]（）\(\)].*?[】\]\)）]', '', text)
    text = text.lower().strip()
    text = re.sub(r'[^\w\s\u4e00-\u9fff]', '', text)
    return text

def get_fuzzy_score(name1, name2):
    n1 = normalize_text(name1)
    n2 = normalize_text(name2)
    if not n1 or not n2:
        return 0
    if n1 in n2 or n2 in n1:
        return 100
    return fuzz.token_set_ratio(n1, n2)

def clean_barcode_series(series):
    bc = series.astype(str).str.strip()
    bc = bc.replace(['nan', 'None', ''], '')
    def clean_one(val):
        if val == '':
            return ''
        try:
            num = float(val)
            if num.is_integer():
                return str(int(num))
            else:
                return val
        except ValueError:
            return val
    return bc.apply(clean_one)

def simple_tokenizer(text):
    words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', str(text))
    return [w.lower() for w in words if len(w) >= 2]

def build_inverted_index(names):
    index = defaultdict(list)
    for idx, name in enumerate(names):
        if pd.isna(name):
            continue
        tokens = set(simple_tokenizer(name))
        for token in tokens:
            index[token].append(idx)
    return index

def round_price_by_interval(value):
    if pd.isna(value):
        return value
    sign = 1 if value >= 0 else -1
    abs_val = abs(value)
    integer_part = math.floor(abs_val)
    decimal = abs_val - integer_part
    if decimal < 0.3:
        new_decimal = 0.2
    elif decimal < 0.6:
        new_decimal = 0.5
    else:
        new_decimal = 0.9
    return sign * (integer_part + new_decimal)

def ceil_to_one_decimal(value):
    if pd.isna(value):
        return value
    return math.ceil(value * 10) / 10

# ----------------------------- 步骤1：匹配商品 ---------------------------------
def step1_match():
    st.header("1️⃣ 商品匹配（内部 vs 外部）")
    col1, col2 = st.columns(2)
    with col1:
        internal_file = st.file_uploader("内部数据（测试版）.csv", type="csv", key="internal")
    with col2:
        external_file = st.file_uploader("外部数据2.csv", type="csv", key="external")
    similarity = st.slider("最低相似度阈值", 0, 100, 40)
    price_range = st.slider("价格区间浮动比例 (±%)", 0, 100, 35) / 100.0
    if st.button("开始匹配", key="match_btn"):
        if internal_file is None or external_file is None:
            st.error("请上传两个文件")
            return
        try:
            internal_df = pd.read_csv(internal_file, encoding='utf-8')
            external_df = pd.read_csv(external_file, encoding='utf-8')
        except Exception as e:
            st.error(f"读取文件失败: {e}")
            return
        
        # 列名映射
        internal = internal_df.copy()
        external = external_df.copy()
        internal.rename(columns={
            '名称': 'internal_name',
            '商品条码': 'internal_barcode',
            '规格': 'internal_spec',
            '总销量': 'total_sales',
            '销售价': 'internal_price',
            '进货价': 'purchase_price'
        }, inplace=True, errors='ignore')
        external.rename(columns={
            '商品名': 'external_name',
            '规格': 'external_spec',
            '条码': 'external_barcode',
            '活动价': 'external_activity_price',
            '原价': 'external_original_price'
        }, inplace=True, errors='ignore')
        
        # 校验必要列
        required_internal = ['internal_name', 'internal_barcode', 'internal_price']
        for col in required_internal:
            if col not in internal.columns:
                st.error(f"内部数据缺少必要列: {col}")
                return
        required_external = ['external_name', 'external_barcode', 'external_original_price']
        for col in required_external:
            if col not in external.columns:
                st.error(f"外部数据缺少必要列: {col}")
                return
        
        # 填充默认列
        for col in ['internal_spec', 'total_sales', 'purchase_price']:
            if col not in internal.columns:
                internal[col] = ''
        for col in ['external_spec', 'external_activity_price']:
            if col not in external.columns:
                external[col] = ''
        
        internal['internal_price'] = pd.to_numeric(internal['internal_price'], errors='coerce')
        external['external_original_price'] = pd.to_numeric(external['external_original_price'], errors='coerce')
        external['external_activity_price'] = pd.to_numeric(external['external_activity_price'], errors='coerce')
        
        internal['internal_barcode'] = clean_barcode_series(internal['internal_barcode'])
        external['external_barcode'] = clean_barcode_series(external['external_barcode'])
        
        # 条码精确匹配字典
        barcode_best = {}
        for barcode, group in external.groupby('external_barcode'):
            if barcode == '':
                continue
            group_sorted = group.sort_values(by='external_original_price', na_position='last')
            barcode_best[barcode] = group_sorted.iloc[0]
        
        external_names = external['external_name'].tolist()
        external_prices = external['external_original_price'].tolist()
        external_activity = external['external_activity_price'].tolist()
        external_specs = external['external_spec'].tolist()
        
        with st.spinner("构建倒排索引..."):
            inverted_index = build_inverted_index(external_names)
        
        results = []
        progress_bar = st.progress(0)
        total = len(internal)
        for idx, (_, int_row) in enumerate(internal.iterrows()):
            progress_bar.progress((idx+1)/total)
            internal_name = int_row['internal_name']
            internal_barcode = int_row['internal_barcode']
            internal_price = int_row['internal_price']
            internal_spec = int_row.get('internal_spec', '')
            total_sales = int_row.get('total_sales', '')
            purchase_price = int_row.get('purchase_price', '')
            matched = False
            match_type = '无匹配'
            matched_ext_row = None
            
            # 条码匹配
            if internal_barcode and internal_barcode in barcode_best:
                matched_ext_row = barcode_best[internal_barcode]
                matched = True
                match_type = '条码匹配'
            
            # 模糊匹配（所有候选必须通过价格区间）
            if not matched and pd.notna(internal_price):
                tokens = set(simple_tokenizer(internal_name))
                candidate_indices = set()
                for token in tokens:
                    if token in inverted_index:
                        candidate_indices.update(inverted_index[token])
                candidates = []
                for ext_idx in candidate_indices:
                    ext_name = external_names[ext_idx]
                    score = get_fuzzy_score(internal_name, ext_name)
                    if score < similarity:
                        continue
                    ext_price = external_prices[ext_idx]
                    if pd.isna(ext_price):
                        continue
                    price_low = internal_price * (1 - price_range)
                    price_high = internal_price * (1 + price_range)
                    if price_low <= ext_price <= price_high:
                        candidates.append((ext_idx, score, ext_price))
                if candidates:
                    candidates.sort(key=lambda x: (-x[1], abs(x[2] - internal_price)))
                    best_idx = candidates[0][0]
                    matched_ext_row = {
                        'external_name': external_names[best_idx],
                        'external_spec': external_specs[best_idx],
                        'external_activity_price': external_activity[best_idx],
                        'external_original_price': external_prices[best_idx]
                    }
                    matched = True
                    match_type = '模糊匹配'
            
            row = {
                '内部商品名称': internal_name,
                '内部规格': internal_spec,
                '内部条码': internal_barcode,
                '内部销售价': internal_price,
                '总销量': total_sales,
                '进货价': purchase_price,
                '外部商品名称': matched_ext_row.get('external_name') if matched_ext_row else None,
                '外部规格': matched_ext_row.get('external_spec', '') if matched_ext_row else None,
                '外部活动价': matched_ext_row.get('external_activity_price') if matched_ext_row else None,
                '外部原价': matched_ext_row.get('external_original_price') if matched_ext_row else None,
                '匹配结果': match_type
            }
            results.append(row)
        
        result_df = pd.DataFrame(results)
        st.session_state['match_result'] = result_df
        st.success("匹配完成！")
        st.dataframe(result_df.head(10))
        output = io.BytesIO()
        result_df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        st.download_button("📥 下载匹配结果 (CSV)", data=output, file_name="匹配结果（全部）3.csv", mime="text/csv")

# ----------------------------- 步骤2：ABC分类 ---------------------------------
def step2_classify():
    st.header("2️⃣ ABC分类（基于销量）")
    use_prev = st.checkbox("使用上一步的匹配结果", value=True, key="classify_use_prev")
    uploaded = None
    if not use_prev:
        uploaded = st.file_uploader("上传匹配结果文件 (匹配结果（全部）3.csv)", type="csv", key="classify_input")
    a_ratio = st.number_input("A类商品数量占比", min_value=0.0, max_value=1.0, value=0.2, step=0.05)
    b_ratio = st.number_input("B类商品数量占比", min_value=0.0, max_value=1.0, value=0.6, step=0.05)
    if st.button("开始分类", key="classify_btn"):
        if use_prev and 'match_result' in st.session_state:
            df = st.session_state['match_result'].copy()
        elif uploaded is not None:
            df = pd.read_csv(uploaded, encoding='utf-8-sig')
        else:
            st.error("没有可用的数据，请上传文件或先执行匹配")
            return
        if '总销量' not in df.columns:
            st.error("文件中没有 '总销量' 列")
            return
        df['总销量'] = pd.to_numeric(df['总销量'], errors='coerce')
        positive_mask = (df['总销量'] > 0) & (df['总销量'].notna())
        if positive_mask.sum() == 0:
            st.warning("没有销量大于0的商品，所有商品归为C类")
            df['商品分类'] = 'C'
        else:
            sales_positive = df.loc[positive_mask, '总销量']
            value_counts = sales_positive.value_counts().sort_index(ascending=False)
            total_positive = len(sales_positive)
            cum_count = 0
            a_boundary = None
            b_boundary = None
            for sales_val, cnt in value_counts.items():
                cum_count += cnt
                ratio = cum_count / total_positive
                if a_boundary is None and ratio >= a_ratio:
                    a_boundary = sales_val
                if b_boundary is None and ratio >= b_ratio:
                    b_boundary = sales_val
                    break
            if b_boundary is None:
                b_boundary = value_counts.index.min()
            def get_category(sales):
                if pd.isna(sales) or sales <= 0:
                    return 'C'
                if sales >= a_boundary:
                    return 'A'
                elif sales >= b_boundary:
                    return 'B'
                else:
                    return 'C'
            df['商品分类'] = df['总销量'].apply(get_category)
            st.info(f"正销量商品总数：{total_positive}，A类边界销量≥{a_boundary}，B类边界销量≥{b_boundary}")
        st.session_state['classified_result'] = df
        st.success("分类完成")
        st.dataframe(df[['内部商品名称', '总销量', '商品分类']].head(10))
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        st.download_button("📥 下载分类结果 (CSV)", data=output, file_name="匹配结果_分类.csv", mime="text/csv")

# ----------------------------- 步骤3：线上价格计算 ---------------------------------
def step3_online_price():
    st.header("3️⃣ 线上原价/活动价计算")
    use_prev = st.checkbox("使用上一步的分类结果", value=True, key="online_use_prev")
    uploaded = None
    if not use_prev:
        uploaded = st.file_uploader("上传分类结果文件 (匹配结果_分类.csv)", type="csv", key="online_input")
    st.subheader("竞品价格调整值（加法）")
    col1, col2, col3 = st.columns(3)
    comp_adj = {}
    with col1:
        comp_adj['A_orig'] = st.number_input("A类原价调整", value=0.0, step=0.1)
        comp_adj['A_act'] = st.number_input("A类活动价调整", value=0.0, step=0.1)
    with col2:
        comp_adj['B_orig'] = st.number_input("B类原价调整", value=0.0, step=0.1)
        comp_adj['B_act'] = st.number_input("B类活动价调整", value=0.0, step=0.1)
    with col3:
        comp_adj['C_orig'] = st.number_input("C类原价调整", value=0.0, step=0.1)
        comp_adj['C_act'] = st.number_input("C类活动价调整", value=0.0, step=0.1)
    st.subheader("自有商品乘数")
    col1, col2, col3 = st.columns(3)
    self_coeffs = {}
    with col1:
        self_coeffs['A_orig'] = st.number_input("A类原价乘数 (×内部销售价)", value=1.0, step=0.1, format="%.2f")
        self_coeffs['A_act'] = st.number_input("A类活动价乘数", value=1.0, step=0.1, format="%.2f")
    with col2:
        self_coeffs['B_orig'] = st.number_input("B类原价乘数 (×进货价)", value=2.5, step=0.1, format="%.2f")
        self_coeffs['B_act'] = st.number_input("B类活动价乘数", value=2.3, step=0.1, format="%.2f")
    with col3:
        self_coeffs['C_orig'] = st.number_input("C类原价乘数 (×进货价)", value=3.5, step=0.1, format="%.2f")
        self_coeffs['C_act'] = st.number_input("C类活动价乘数", value=2.5, step=0.1, format="%.2f")
    if st.button("计算线上价格", key="online_btn"):
        if use_prev and 'classified_result' in st.session_state:
            df = st.session_state['classified_result'].copy()
        elif uploaded is not None:
            df = pd.read_csv(uploaded, encoding='utf-8-sig')
        else:
            st.error("没有数据，请先分类或上传文件")
            return
        required = ['匹配结果', '商品分类', '内部销售价', '进货价', '外部原价', '外部活动价']
        for col in required:
            if col not in df.columns:
                st.error(f"缺少列: {col}")
                return
        df['内部销售价'] = pd.to_numeric(df['内部销售价'], errors='coerce')
        df['进货价'] = pd.to_numeric(df['进货价'], errors='coerce')
        df['外部原价'] = pd.to_numeric(df['外部原价'], errors='coerce')
        df['外部活动价'] = pd.to_numeric(df['外部活动价'], errors='coerce')
        def compute(row):
            match_res = row['匹配结果']
            cat = row['商品分类']
            internal_sale = row['内部销售价']
            purchase = row['进货价']
            ext_orig = row['外部原价']
            ext_act = row['外部活动价']
            if match_res == '无匹配':  # 自有
                if cat == 'A':
                    if pd.isna(internal_sale):
                        return (None, None)
                    orig = internal_sale * self_coeffs['A_orig']
                    act = internal_sale * self_coeffs['A_act']
                elif cat == 'B':
                    if pd.isna(purchase):
                        return (None, None)
                    orig = purchase * self_coeffs['B_orig']
                    act = purchase * self_coeffs['B_act']
                else:  # C
                    if pd.isna(purchase):
                        return (None, None)
                    orig = purchase * self_coeffs['C_orig']
                    act = purchase * self_coeffs['C_act']
                return (orig, act)
            else:  # 竞品
                if pd.isna(ext_orig):
                    return (None, None)
                if pd.isna(ext_act):
                    ext_act = ext_orig
                adj_orig = comp_adj[f'{cat}_orig']
                adj_act = comp_adj[f'{cat}_act']
                if ext_act > ext_orig:
                    return (ext_orig + adj_orig, "#N/A")
                else:
                    return (ext_orig + adj_orig, ext_act + adj_act)
        price_pairs = df.apply(compute, axis=1)
        df['线上原价_raw'] = price_pairs.apply(lambda x: x[0])
        df['线上活动价_raw'] = price_pairs.apply(lambda x: x[1])
        df['线上原价'] = df['线上原价_raw'].apply(lambda x: round_price_by_interval(x) if not isinstance(x, str) else x)
        df['线上活动价'] = df['线上活动价_raw'].apply(lambda x: round_price_by_interval(x) if not isinstance(x, str) else x)
        df.drop(['线上原价_raw', '线上活动价_raw'], axis=1, inplace=True)
        st.session_state['online_priced'] = df
        st.success("线上价格计算完成")
        st.dataframe(df[['内部商品名称', '线上原价', '线上活动价']].head(10))
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        st.download_button("📥 下载定价结果 (CSV)", data=output, file_name="匹配结果_定价.csv", mime="text/csv")

# ----------------------------- 步骤4：线下价格计算 ---------------------------------
def step4_offline_price():
    st.header("4️⃣ 线下价格计算")
    use_prev = st.checkbox("使用上一步的线上定价结果", value=True, key="offline_use_prev")
    uploaded = None
    if not use_prev:
        uploaded = st.file_uploader("上传线上定价文件 (匹配结果_定价.csv)", type="csv", key="offline_input")
    st.subheader("系数设置")
    low_thresh = st.number_input("低毛利阈值", value=0.10, step=0.01, format="%.2f")
    high_thresh = st.number_input("高毛利阈值", value=0.65, step=0.01, format="%.2f")
    low_ratio = st.number_input("低毛利分母 (进货价 / ?)", value=0.8, step=0.05, format="%.2f")
    mid_ratio = st.number_input("中毛利系数 (线上原价 × ?)", value=0.6, step=0.05, format="%.2f")
    high_ratio = st.number_input("高毛利分母 (进货价 / ?)", value=0.35, step=0.05, format="%.2f")
    fixed20_denom = st.number_input("固定20%毛利分母", value=0.8, step=0.05, format="%.2f")
    fixed15_denom = st.number_input("固定15%毛利分母", value=0.85, step=0.05, format="%.2f")
    fixed20_file = st.file_uploader("固定20%毛利商品 (条码列表) - 可选", type="csv", key="fixed20")
    fixed15_file = st.file_uploader("固定15%毛利商品 (条码列表) - 可选", type="csv", key="fixed15")
    if st.button("计算线下价格", key="offline_btn"):
        if use_prev and 'online_priced' in st.session_state:
            df = st.session_state['online_priced'].copy()
        elif uploaded is not None:
            df = pd.read_csv(uploaded, encoding='utf-8-sig')
        else:
            st.error("没有数据")
            return
        required = ['内部条码', '进货价', '线上原价']
        for col in required:
            if col not in df.columns:
                st.error(f"缺少列: {col}")
                return
        df['进货价'] = pd.to_numeric(df['进货价'], errors='coerce')
        df['线上原价'] = pd.to_numeric(df['线上原价'], errors='coerce')
        df['内部条码'] = df['内部条码'].astype(str).str.strip()
        fixed20_barcodes = set()
        if fixed20_file:
            df20 = pd.read_csv(fixed20_file, encoding='utf-8-sig')
            if '条码' in df20.columns:
                fixed20_barcodes = set(df20['条码'].astype(str).str.strip())
        fixed15_barcodes = set()
        if fixed15_file:
            df15 = pd.read_csv(fixed15_file, encoding='utf-8-sig')
            if '条码' in df15.columns:
                fixed15_barcodes = set(df15['条码'].astype(str).str.strip())
        def compute(row):
            barcode = row['内部条码']
            purchase = row['进货价']
            online = row['线上原价']
            if pd.isna(purchase) or purchase <= 0:
                return (None, "无有效进货价")
            if barcode in fixed20_barcodes:
                return (purchase / fixed20_denom, "固定毛利20%")
            if barcode in fixed15_barcodes:
                return (purchase / fixed15_denom, "固定毛利15%")
            if pd.isna(online) or online <= 0:
                return (None, "线上原价无效")
            denominator = online * mid_ratio
            if denominator <= 0:
                return (None, "分母无效")
            gross_margin = (denominator - purchase) / denominator
            if gross_margin <= low_thresh:
                return (purchase / low_ratio, "普通商品(低毛利)")
            elif gross_margin < high_thresh:
                return (online * mid_ratio, "普通商品(中毛利)")
            else:
                return (purchase / high_ratio, "普通商品(高毛利)")
        result = df.apply(compute, axis=1)
        df['线下价格_raw'] = result.apply(lambda x: x[0])
        df['定价类型'] = result.apply(lambda x: x[1])
        df['线下价格'] = df['线下价格_raw'].apply(round_price_by_interval)
        df.drop('线下价格_raw', axis=1, inplace=True)
        st.session_state['offline_priced'] = df
        st.success("线下价格计算完成")
        st.dataframe(df[['内部商品名称', '线下价格', '定价类型']].head(10))
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        st.download_button("📥 下载线下定价结果", data=output, file_name="匹配结果_线下定价.csv", mime="text/csv")

# ----------------------------- 步骤5：小程序价格计算 ---------------------------------
def step5_miniprogram():
    st.header("5️⃣ 小程序价格计算")
    use_prev = st.checkbox("使用上一步的线下定价结果", value=True, key="mp_use_prev")
    uploaded = None
    if not use_prev:
        uploaded = st.file_uploader("上传线下定价文件 (匹配结果_线下定价.csv)", type="csv", key="mp_input")
    base_multiplier = st.number_input("线下价格 × 倍数（基准）", value=1.1, step=0.05, format="%.2f")
    denom = st.number_input("进货价 / 分母阈值（比较线上活动价）", value=0.8, step=0.05, format="%.2f")
    if st.button("计算小程序价格", key="mp_btn"):
        if use_prev and 'offline_priced' in st.session_state:
            df = st.session_state['offline_priced'].copy()
        elif uploaded is not None:
            df = pd.read_csv(uploaded, encoding='utf-8-sig')
        else:
            st.error("没有数据")
            return
        required = ['线下价格', '线上活动价', '进货价']
        for col in required:
            if col not in df.columns:
                st.error(f"缺少列: {col}")
                return
        df['线下价格'] = pd.to_numeric(df['线下价格'], errors='coerce')
        df['进货价'] = pd.to_numeric(df['进货价'], errors='coerce')
        def is_valid_activity(val):
            if pd.isna(val):
                return False
            if isinstance(val, str) and val.strip().upper() == '#N/A':
                return False
            try:
                float(val)
                return True
            except:
                return False
        def compute(row):
            offline = row['线下价格']
            activity = row['线上活动价']
            purchase = row['进货价']
            if pd.isna(offline) or offline <= 0:
                return None
            temp = offline * base_multiplier
            if not is_valid_activity(activity):
                return temp
            else:
                act_price = float(activity)
                if temp > act_price:
                    if pd.isna(purchase) or purchase <= 0:
                        return temp
                    threshold = purchase / denom
                    if act_price > threshold:
                        return act_price
                    else:
                        return threshold
                else:
                    return temp
        df['小程序价格_raw'] = df.apply(compute, axis=1)
        df['小程序价格'] = df['小程序价格_raw'].apply(round_price_by_interval)
        df.drop('小程序价格_raw', axis=1, inplace=True)
        st.session_state['miniprogram_priced'] = df
        st.success("小程序价格计算完成")
        st.dataframe(df[['内部商品名称', '小程序价格']].head(10))
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        st.download_button("📥 下载小程序定价结果", data=output, file_name="匹配结果_小程序定价.csv", mime="text/csv")

# ----------------------------- 步骤6：最终替换 ---------------------------------
def step6_final():
    st.header("6️⃣ 最终价格替换（促销价/零售价）")
    use_prev = st.checkbox("使用上一步的小程序定价结果", value=True, key="final_use_prev")
    uploaded = None
    if not use_prev:
        uploaded = st.file_uploader("上传小程序定价文件 (匹配结果_小程序定价.csv)", type="csv", key="final_input")
    promo_file = st.file_uploader("固定促销价.csv (条码,固定促销价)", type="csv", key="promo")
    retail_file = st.file_uploader("固定零售价.csv (条码,建议零售价)", type="csv", key="retail")
    if st.button("执行替换", key="final_btn"):
        if use_prev and 'miniprogram_priced' in st.session_state:
            df = st.session_state['miniprogram_priced'].copy()
        elif uploaded is not None:
            df = pd.read_csv(uploaded, encoding='utf-8-sig')
        else:
            st.error("没有数据")
            return
        required = ['内部条码', '线下价格', '小程序价格', '定价类型']
        for col in required:
            if col not in df.columns:
                st.error(f"缺少列: {col}")
                return
        df['内部条码'] = df['内部条码'].astype(str).str.strip()
        promo_dict = {}
        if promo_file:
            df_p = pd.read_csv(promo_file, encoding='utf-8-sig')
            if '条码' in df_p.columns and '固定促销价' in df_p.columns:
                for _, r in df_p.iterrows():
                    bc = str(r['条码']).strip()
                    if bc:
                        try:
                            promo_dict[bc] = float(r['固定促销价'])
                        except:
                            pass
        retail_dict = {}
        if retail_file:
            df_r = pd.read_csv(retail_file, encoding='utf-8-sig')
            if '条码' in df_r.columns and '建议零售价' in df_r.columns:
                for _, r in df_r.iterrows():
                    bc = str(r['条码']).strip()
                    if bc:
                        try:
                            retail_dict[bc] = float(r['建议零售价'])
                        except:
                            pass
        replaced = 0
        for idx, row in df.iterrows():
            bc = row['内部条码']
            if not bc:
                continue
            if bc in promo_dict:
                price = promo_dict[bc]
                df.at[idx, '线下价格'] = ceil_to_one_decimal(price)
                df.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
                df.at[idx, '定价类型'] = "固定促销价"
                replaced += 1
            elif bc in retail_dict:
                price = retail_dict[bc]
                df.at[idx, '线下价格'] = ceil_to_one_decimal(price)
                df.at[idx, '小程序价格'] = ceil_to_one_decimal(price)
                df.at[idx, '定价类型'] = "建议零售价"
                replaced += 1
        st.success(f"替换完成，共替换 {replaced} 条")
        st.dataframe(df[['内部商品名称', '线下价格', '小程序价格', '定价类型']].head(10))
        output = io.BytesIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')
        output.seek(0)
        st.download_button("📥 下载最终定价结果", data=output, file_name="匹配结果_最终定价.csv", mime="text/csv")

# ----------------------------- 主界面导航 ---------------------------------
st.sidebar.header("工作流导航")
step = st.sidebar.radio("选择步骤", ["1.商品匹配", "2.ABC分类", "3.线上价格", "4.线下价格", "5.小程序价格", "6.最终替换"])

if step == "1.商品匹配":
    step1_match()
elif step == "2.ABC分类":
    step2_classify()
elif step == "3.线上价格":
    step3_online_price()
elif step == "4.线下价格":
    step4_offline_price()
elif step == "5.小程序价格":
    step5_miniprogram()
elif step == "6.最终替换":
    step6_final()
