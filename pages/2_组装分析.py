import streamlit as st
import pandas as pd
import re
import math
import io
import warnings

warnings.filterwarnings("ignore")

st.set_page_config(page_title="组装商品分析工具", layout="centered")
st.title("📊 组装商品销量与库存分析")
st.markdown("上传组装拆分表和商品数据表，自动计算每个组装商品的上下限。")

# ===============================
# 原有的 ProductProcessor 类（略作修改，支持从DataFrame读取）
# ===============================

class ProductProcessor:
    def __init__(self, barcode_df, stock_df):
        # 直接传入两个 DataFrame，不再从文件读取
        self.barcode_df = barcode_df.copy()
        self.stock_df = stock_df.copy()

        # 去空格
        self.barcode_df.columns = self.barcode_df.columns.str.strip()
        self.stock_df.columns = self.stock_df.columns.str.strip()

        # 自动识别列名
        def find_column(df, keywords):
            for col in df.columns:
                for key in keywords:
                    if key in col:
                        return col
            return None

        name_col = find_column(self.stock_df, ["名称", "商品名称", "品名"])
        barcode_col = find_column(self.stock_df, ["条码", "商品条码", "barcode"])
        stock_col = find_column(self.stock_df, ["库存"])
        sales_col = find_column(self.stock_df, ["销量"])
        unit_col = find_column(self.stock_df, ["单位"])
        spec_col = find_column(self.stock_df, ["规格"])

        if not name_col or not barcode_col:
            st.error("❌ 未识别到【名称】或【条码】列，请检查Excel")
            st.stop()

        self.stock_df = self.stock_df.rename(columns={
            name_col: "名称",
            barcode_col: "条码",
            stock_col: "库存",
            sales_col: "销量",
            unit_col: "主单位",
            spec_col: "规格"
        })

        # 数据清洗
        self.stock_df["名称"] = self.stock_df["名称"].fillna("").astype(str)
        self.stock_df["条码"] = self.stock_df["条码"].fillna("").astype(str)
        self.stock_df["库存"] = pd.to_numeric(self.stock_df.get("库存", 0), errors="coerce").fillna(0)
        self.stock_df["销量"] = pd.to_numeric(self.stock_df.get("销量", 0), errors="coerce").fillna(0)

        self.all_barcodes = self._extract_all_barcodes()

    def _extract_all_barcodes(self):
        # 组装拆分表：第一列是大件条码，第三列是小件条码
        big_codes = set(self.barcode_df.iloc[:, 0].astype(str))
        small_codes = set(self.barcode_df.iloc[:, 2].astype(str))
        return big_codes | small_codes

    def is_assembled(self, barcode):
        return str(barcode) in self.all_barcodes

    def parse_specification(self, name):
        name = str(name)
        if "*" in name:
            last_part = name.split("*")[-1]
        else:
            return 1
        match = re.findall(r'(\d+)', last_part)
        return int(match[0]) if match else 1

    def get_base_name(self, name):
        return re.sub(r'[*xX×]\d+', '', str(name)).strip()

    def calculate_group(self, group):
        total_sales = 0
        total_stock = 0
        max_spec = 1
        detail_list = []

        for _, row in group.iterrows():
            name = row["名称"]
            sales = int(float(row["销量"]))
            stock = int(float(row.get("库存", 0)))
            spec = self.parse_specification(name)
            max_spec = max(max_spec, spec)
            total_sales += sales * spec
            total_stock += stock * spec
            detail_list.append({
                "商品名称": name,
                "商品销量": sales,
                "库存": stock
            })

        if max_spec == 0:
            max_spec = 1

        sales_units = round(total_sales / max_spec, 2)
        stock_units = round(total_stock / max_spec, 2)

        return total_sales, total_stock, max_spec, sales_units, stock_units, detail_list

    def run(self):
        assembled_rows = []
        normal_rows = []

        for _, row in self.stock_df.iterrows():
            if self.is_assembled(row["条码"]):
                assembled_rows.append(row)
            else:
                normal_rows.append(row)

        assembled_df = pd.DataFrame(assembled_rows)
        result = []

        # 组装拆分
        if not assembled_df.empty:
            assembled_df["base_name"] = assembled_df["名称"].apply(self.get_base_name)
            grouped = assembled_df.groupby("base_name")

            for _, group in grouped:
                total_sales, total_stock, max_spec, sales_units, stock_units, detail_list = self.calculate_group(group)

                for _, row in group.iterrows():
                    name = row["名称"]
                    spec = self.parse_specification(name)
                    upper = 0
                    lower = 0

                    if total_sales != 0 and spec == 1:
                        if total_sales / 6 > max_spec:
                            upper = max_spec * 2
                            lower = max_spec
                        else:
                            upper = max_spec
                            lower = math.ceil(max_spec / 2)

                    result.append({
                        "商品名称": name,
                        "商品条码": row["条码"],
                        "规格": row.get("规格", ""),
                        "单位": row.get("主单位", ""),
                        "库存数量": row.get("库存", 0),
                        "总库存（最小单位）": total_stock,
                        "库存（件）": stock_units,
                        "明细": detail_list,
                        "总销量": total_sales,
                        "销量（件）": sales_units,
                        "上限": upper,
                        "下限": lower,
                        "是否为组装拆分": "组装拆分"
                    })

        # 非组装
        for row in normal_rows:
            result.append({
                "商品名称": row["名称"],
                "商品条码": row["条码"],
                "规格": row.get("规格", ""),
                "单位": row.get("主单位", ""),
                "库存数量": row.get("库存", 0),
                "总库存（最小单位）": row.get("库存", 0),
                "库存（件）": row.get("库存", 0),
                "明细": [],
                "总销量": row.get("销量", 0),
                "销量（件）": row.get("销量", 0),
                "上限": 0,
                "下限": 0,
                "是否为组装拆分": "否"
            })

        return pd.DataFrame(result)


# ===============================
# 生成商品数据表模板（提供下载）
# ===============================
@st.cache_data
def generate_template():
    # 定义列名
    columns = ["名称", "条码", "规格", "主单位", "库存", "销量"]
    # 创建一行示例数据
    sample_row = ["示例商品A", "6901234567890", "-", "个", 10, 5]
    df = pd.DataFrame([sample_row], columns=columns)
    return df

# 模板下载按钮放在侧边栏或顶部，这里放在侧边栏
with st.sidebar:
    st.markdown("### 📂 模板下载")
    st.markdown("如果还没有商品数据表，可以下载模板：")
    template_df = generate_template()
    template_excel = io.BytesIO()
    with pd.ExcelWriter(template_excel, engine='openpyxl') as writer:
        template_df.to_excel(writer, index=False, sheet_name="商品数据模板")
    template_excel.seek(0)
    st.download_button(
        label="📥 下载商品数据表模板(.xlsx)",
        data=template_excel,
        file_name="商品数据表模板.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ===============================
# 主界面文件上传与计算
# ===============================
barcode_file = st.file_uploader("1️⃣ 上传组装拆分表 (Excel)", type=["xlsx", "xls"], key="barcode2")
stock_file = st.file_uploader("2️⃣ 上传商品数据表 (Excel)", type=["xlsx", "xls"], key="stock2")

if barcode_file and stock_file:
    if st.button("📈 开始分析"):
        with st.spinner("正在计算，请稍候..."):
            try:
                barcode_df = pd.read_excel(barcode_file)
                stock_df = pd.read_excel(stock_file)

                processor = ProductProcessor(barcode_df, stock_df)
                result_df = processor.run()

                st.success("分析完成！")
                st.subheader("分析结果预览")
                st.dataframe(result_df)

                # 下载结果按钮
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    result_df.to_excel(writer, index=False, sheet_name="分析结果")
                output.seek(0)
                st.download_button(
                    label="📥 下载分析结果.xlsx",
                    data=output,
                    file_name="组装分析结果.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"出错了：{e}")
else:
    st.info("请上传两个 Excel 文件，然后点击开始分析。")
