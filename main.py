"""Streamlit entry point — financial model knowledge graph explorer."""
import streamlit as st

st.set_page_config(
    page_title="财务模型知识图谱",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 财务模型知识图谱系统")
st.markdown("""
欢迎使用财务模型知识图谱系统。请从左侧导航栏选择功能：

| 页面 | 功能 |
|------|------|
| 📁 上传解析 | 上传 Excel 财务模型，解析为三层知识图谱 |
| 🔍 图谱浏览 | 交互式浏览 Cell / Indicator / Table 层 |
| ⚙️ 参数重算 | 修改参数，触发全模型增量重算 |
| 📊 快照对比 | 对比两个快照，查看变化传播链 |
| 💬 智能问答 | 基于图谱的 LLM 财务问答 |
""")
