import streamlit as st
import pandas as pd
import re
import io


# ===============================
# 你原来的所有函数（完全不用改）
# ===============================

def clean_barcode(x):
    try:
        if pd.isnull(x):
            return ""
        return str(int(float(x)))
    except:
        return str(x).strip()


def parse_spec(name):
    if "*" not in name:
        return 1
    try:
        return int(name.rsplit("*", 1)[-1])
    except:
        return 1


def is_combination(barcode, barcode_df):
    return barcode in barcode_df["小件商品条码"].values


def get_all_specs(product_name, product_df):
    base_name = re.split(r"\*", product_name)[0]
    return product_df[product_df["名称（必填）"].str.startswith(base_name)].copy()


def calculate_combination(spec_df):
    spec_df["规格数"] = spec_df["名称（必填）"].apply(parse_spec)
    spec_df["库存量"] = spec_df["库存量"].fillna(0)
    spec_df["换算库存"] = spec_df["库存量"].astype(float) * spec_df["规格数"]
    total_stock = int(spec_df["换算库存"].sum())
    min_row = spec_df.loc[spec_df["规格数"].idxmin()]
    min_stock = int(float(min_row.get("库存下限", 1)) or 1)
    max_spec = int(spec_df["规格数"].max())
    if total_stock < min_stock:
        return total_stock, "是", max_spec
    else:
        return total_stock, "否", 0


def handle_normal_product(barcode, product_df):
    match = product_df[product_df["匹配条码"] == barcode]
    if match.empty:
        # 在 Web 上不打印，而是记录警告（可以用 st.warning，后面处理）
        return 0, ""
    stock_val = match.iloc[0]["库存量"]
    category = match.iloc[0]["分类（必填）"]
    try:
        stock_val = int(float(stock_val))
    except:
        stock_val = 0
    return stock_val, category


# ===============================
# 主处理函数（接收上传的文件对象，返回结果 DataFrame）
# ===============================
def process_files(purchase_file, barcode_file, product_file):
    # 读取上传的 Excel 文件（都是 BytesIO 对象）
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
    # 用于收集未匹配条码的警告（避免在循环里直接 st.warning 重复弹窗）
    unmatched_barcodes = []

    for _, row in purchase_df.iterrows():
        product_name = row["品名"]
        barcode = row["条码"]
        purchase_qty = int(float(row.get("采购量", 0)))
        purchase_price = round(float(row.get("采购单价", 0)), 2)

        if is_combination(barcode, barcode_df):
            spec_df = get_all_specs(product_name, product_df)
            if spec_df.empty:
                # 组装商品找不到对应规格，记录警告
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

    # 将未匹配警告显示在 Streamlit 上
    if unmatched_barcodes:
        for warn in unmatched_barcodes:
            st.warning(warn)

    result_df = pd.DataFrame(results)
    return result_df


# ===============================
# Streamlit 界面
# ===============================
st.set_page_config(page_title="补货计算工具", layout="centered")
st.title("📦 采购补货计算工具")
st.markdown("请上传三个必需的 Excel 文件：**采购订单**、**组装拆分表**、**商品资料**")

# 三个独立的上传组件
purchase_file = st.file_uploader("1. 采购订单 (Excel)", type=["xlsx", "xls"], key="purchase")
barcode_file = st.file_uploader("2. 组装拆分表 (Excel)", type=["xlsx", "xls"], key="barcode")
product_file = st.file_uploader("3. 商品资料 (Excel)", type=["xlsx", "xls"], key="product")

# 当三个文件都上传后，显示“开始计算”按钮
if purchase_file and barcode_file and product_file:
    if st.button("🚀 开始计算补货结果"):
        with st.spinner("正在处理，请稍候..."):
            try:
                result_df = process_files(purchase_file, barcode_file, product_file)
                st.success("计算完成！")

                # 显示结果表格
                st.subheader("补货结果预览")
                st.dataframe(result_df)

                # 提供下载按钮
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