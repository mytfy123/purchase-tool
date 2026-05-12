import streamlit as st
import pandas as pd
import re
import io


# ===============================
# 清洗工具函数（无需修改）
# ===============================

def clean_barcode(x):
    try:
        if pd.isnull(x):
            return ""
        return str(int(float(x)))
    except:
        return str(x).strip()


def parse_spec(name):
    """从商品名称中提取规格数（*后面的数字），无*则返回1"""
    if "*" not in name:
        return 1
    try:
        return int(name.rsplit("*", 1)[-1])
    except:
        return 1


def is_combination(barcode, barcode_df):
    """判断条码是否为组装商品"""
    return barcode in barcode_df["小件商品条码"].values


def get_all_specs(product_name, product_df):
    """获取同一品名下所有规格子商品"""
    base_name = re.split(r"\*", product_name)[0]
    return product_df[product_df["名称（必填）"].str.startswith(base_name)].copy()


# ===============================
# 修改后的核心计算函数（条件不变，补货量按上限和倍数计算）
# ===============================
def calculate_combination(spec_df):
    """
    计算组装商品的补货量。
    返回：总库存, 是否需要补货, 最终补货数量
    """
    spec_df = spec_df.copy()
    spec_df["规格数"] = spec_df["名称（必填）"].apply(parse_spec)
    spec_df["库存量"] = spec_df["库存量"].fillna(0)
    spec_df["换算库存"] = spec_df["库存量"].astype(float) * spec_df["规格数"]

    total_stock = int(spec_df["换算库存"].sum())            # 最小单位库存总量
    max_spec = int(spec_df["规格数"].max())                 # 最大规格数

    # --- 沿用原始判断逻辑：取最小规格那一行的“库存下限” ---
    min_spec_row = spec_df.loc[spec_df["规格数"].idxmin()]
    min_stock = int(float(min_spec_row.get("库存下限", 1)) or 1)   # 默认值为1

    # --- 判断是否需要补货 ---
    if total_stock < min_stock:
        need = "是"
        # --- 补货数量基于“库存上限”计算 ---
        upper_limit = spec_df.iloc[0].get("库存上限", 0)
        try:
            upper_limit = int(float(upper_limit))
        except (ValueError, TypeError):
            upper_limit = 0

        if upper_limit > 0 and total_stock < upper_limit:
            deficit = upper_limit - total_stock
            # 向上取整为最大规格的倍数
            replenish = ((deficit + max_spec - 1) // max_spec) * max_spec
        else:
            # 库存上限为空、为0或已达上限，尽管触发了补货条件，但无明确上限可参照
            replenish = 0
        return total_stock, need, replenish
    else:
        return total_stock, "否", 0


def handle_normal_product(barcode, product_df):
    """处理普通商品：返回库存量和分类"""
    match = product_df[product_df["匹配条码"] == barcode]
    if match.empty:
        return 0, ""
    stock_val = match.iloc[0]["库存量"]
    category = match.iloc[0]["分类（必填）"]
    try:
        stock_val = int(float(stock_val))
    except:
        stock_val = 0
    return stock_val, category


# ===============================
# 主处理流程
# ===============================
def process_files(purchase_file, barcode_file, product_file):
    purchase_df = pd.read_excel(purchase_file)
    barcode_df = pd.read_excel(barcode_file)
    product_df = pd.read_excel(product_file)

    # 清洗条码
    purchase_df["条码"] = purchase_df["条码"].apply(clean_barcode)
    barcode_df["小件商品条码"] = barcode_df["小件商品条码"].apply(clean_barcode)
    product_df["主编码"] = product_df["主编码"].apply(clean_barcode)
    product_df["条码"] = product_df["条码"].apply(clean_barcode)
    product_df["匹配条码"] = product_df["主编码"]
    product_df.loc[product_df["匹配条码"] == "", "匹配条码"] = product_df["条码"]

    results = []
    unmatched_barcodes = []

    for _, row in purchase_df.iterrows():
        product_name = row["品名"]
        barcode = row["条码"]
        purchase_qty = int(float(row.get("采购量", 0)))
        purchase_price = round(float(row.get("采购单价", 0)), 2)

        if is_combination(barcode, barcode_df):
            spec_df = get_all_specs(product_name, product_df)
            if spec_df.empty:
                unmatched_barcodes.append(f"组装商品「{product_name}」未找到规格子商品")
                continue
            total_stock, need, qty = calculate_combination(spec_df)
            result = {
                "商品名称": product_name,
                "商品条码": barcode,
                "商品分类": spec_df.iloc[0]["分类（必填）"],
                "总库存": total_stock,
                "是否需要补货": need,
                "最终补货数量": qty,
                "采购数量": purchase_qty,
                "采购单价": purchase_price
            }
        else:
            total_stock, category = handle_normal_product(barcode, product_df)
            result = {
                "商品名称": product_name,
                "商品条码": barcode,
                "商品分类": category,
                "总库存": total_stock,
                "是否需要补货": "不需要组装拆分",
                "最终补货数量": purchase_qty,
                "采购数量": purchase_qty,
                "采购单价": purchase_price
            }
        results.append(result)

    if unmatched_barcodes:
        for warn in unmatched_barcodes:
            st.warning(warn)

    return pd.DataFrame(results)


# ===============================
# Streamlit 界面
# ===============================
st.set_page_config(page_title="补货计算工具", layout="centered")
st.title("📦 采购补货计算工具")
st.markdown("请上传三个必需的 Excel 文件：**采购订单**、**组装拆分表**、**商品资料**")

purchase_file = st.file_uploader("1. 采购订单 (Excel)", type=["xlsx", "xls"], key="purchase")
barcode_file = st.file_uploader("2. 组装拆分表 (Excel)", type=["xlsx", "xls"], key="barcode")
product_file = st.file_uploader("3. 商品资料 (Excel)", type=["xlsx", "xls"], key="product")

if purchase_file and barcode_file and product_file:
    if st.button("🚀 开始计算补货结果"):
        with st.spinner("正在处理，请稍候..."):
            try:
                result_df = process_files(purchase_file, barcode_file, product_file)
                st.success("计算完成！")
                st.subheader("补货结果预览")
                st.dataframe(result_df)

                # 提供下载
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    result_df.to_excel(writer, index=False, sheet_name="补货结果")
                output.seek(0)
                st.download_button(
                    label="📥 下载补货结果.xlsx",
                    data=output,
                    file_name="补货结果.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"处理出错：{e}")
else:
    st.info("请依次上传三个 Excel 文件，然后点击开始计算。")
