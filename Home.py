import streamlit as st

st.set_page_config(page_title="智能补货工具箱", page_icon="🧰")
st.title("🧰 智能补货与分析工具箱")

st.markdown("""
欢迎使用！请从左侧边栏选择需要的工具：

- **📦 补货计算**：基于采购订单、组装拆分表和商品资料，生成补货建议。
- **📊 组装分析**：基于商品数据表和组装拆分表，分析销量库存并生成上下限。

点击菜单即可开始。
""")