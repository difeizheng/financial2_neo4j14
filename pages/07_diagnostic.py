"""Page 7: Excel diagnostic tools — diff analysis & structure check."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from financial_kg.diagnostic import ExcelDiffAnalyzer, ExcelStructureChecker, fix_structure_defects

st.set_page_config(page_title="Excel 诊断工具", page_icon="🔬", layout="wide")
st.title("🔬 Excel 诊断工具")

# ── Severity / type display config ─────────────────────────────────────────
_SEVERITY = {
    "critical": ("严重", "🔴"),
    "warning": ("警告", "🟡"),
    "info": ("信息", "🔵"),
}
_CAUSE_TYPE = {
    "formula_changed": ("公式变更", "📝"),
    "cache_loss": ("缓存丢失", "⚠️"),
    "value_changed": ("值变更", "🔢"),
    "propagation_break": ("传播链断裂", "🔗"),
}
_DEFECT_TYPE = {
    "static_should_be_formula": ("静态值应为公式", "📐"),
    "propagation_break": ("传播链断裂", "🔗"),
    "inconsistent_block": ("块结构不一致", "📦"),
}


def _save_upload(uploaded_file) -> str:
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(uploaded_file.getvalue())
        return f.name


# ── Tab 1: Diff Root Cause ─────────────────────────────────────────────────
def _render_diff_tab() -> None:
    st.markdown("比较两个勾稽关系的 Excel 文件，自动检测差异类型并定位根因。")
    st.caption("支持检测：公式变更 · openpyxl 缓存丢失 · 值变更 · 传播链断裂")

    col1, col2 = st.columns(2)
    with col1:
        file_a = st.file_uploader(
            "参考文件（原始）", type=["xlsx"], key="diff_a",
        )
        if file_a:
            st.caption(f"📄 {file_a.name} ({file_a.size / 1024:.0f} KB)")
    with col2:
        file_b = st.file_uploader(
            "对比文件（修改后）", type=["xlsx"], key="diff_b",
        )
        if file_b:
            st.caption(f"📄 {file_b.name} ({file_b.size / 1024:.0f} KB)")

    if not file_a or not file_b:
        st.info("请上传两个 Excel 文件以开始分析。")
        return

    if not st.button("🔍 开始分析", type="primary", key="diff_run"):
        return

    path_a = _save_upload(file_a)
    path_b = _save_upload(file_b)
    try:
        with st.spinner("逐 sheet 逐 cell 对比中，请稍候..."):
            analyzer = ExcelDiffAnalyzer()
            report = analyzer.analyze(path_a, path_b)
    except Exception as e:
        st.error(f"分析失败: {e}")
        return
    finally:
        Path(path_a).unlink(missing_ok=True)
        Path(path_b).unlink(missing_ok=True)

    # ── summary metrics ──
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("差异总数", f"{report.total_differences:,}")
    c2.metric("根因数量", len(report.root_causes))
    c3.metric("Roundtrip 缓存丢失", f"{report.roundtrip_lost:,}")

    if report.tool_is_cause:
        st.warning(
            f"⚠️ **openpyxl 缓存丢失**：roundtrip 测试丢失了 "
            f"{report.roundtrip_lost:,} 个公式缓存值。差异可能由工具（openpyxl）引起，"
            "建议使用 XML 级别操作 (zipfile+lxml) 保留缓存值。"
        )
    else:
        st.success("✅ Roundtrip 测试通过，差异非工具引起。")

    # ── root causes ──
    st.subheader("根因详情")
    if not report.root_causes:
        st.info("未找到根因。")
        return

    for i, rc in enumerate(report.root_causes, 1):
        cfg = _CAUSE_TYPE.get(rc.type, (rc.type, "❓"))
        header = f"{cfg[1]} 根因 #{i}: {cfg[0]} — {rc.source}"
        with st.expander(header, expanded=(i == 1)):
            st.markdown(f"**类型**: {cfg[0]}")
            st.markdown(rc.detail)

            if rc.cells:
                label = "受影响单元格"
                n = len(rc.cells)
                st.markdown(f"**{label}** ({n} 个):")
                st.dataframe(
                    [{"单元格": c} for c in rc.cells[:100]],
                    use_container_width=True, hide_index=True,
                )
                if n > 100:
                    st.caption(f"… 仅显示前 100 个，共 {n} 个")

            if rc.path:
                st.markdown("**传播路径**: " + " → ".join(rc.path))


# ── Tab 2: Structure Check ─────────────────────────────────────────────────
def _render_defect_results(report) -> None:
    """Render defect report + fix button. Called from session state."""
    crit = sum(1 for d in report.defects if d.severity == "critical")
    warn = sum(1 for d in report.defects if d.severity == "warning")
    info = sum(1 for d in report.defects if d.severity == "info")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("检测到的 SUMIF 块", report.total_blocks)
    c2.metric("缺陷总数", len(report.defects))
    c3.metric("🔴 严重", crit)
    c4.metric("🟡⚠️ 警告 / 信息", f"{warn} / {info}")

    if not report.defects:
        st.success("✅ 未发现结构缺陷。")
        return

    # ── defect overview table ──
    st.subheader("缺陷概览")
    rows = []
    for d in report.defects:
        sev = _SEVERITY.get(d.severity, (d.severity, "❓"))
        typ = _DEFECT_TYPE.get(d.type, (d.type, "❓"))
        rows.append({
            "级别": f"{sev[1]} {sev[0]}",
            "类型": f"{typ[1]} {typ[0]}",
            "Sheet": d.sheet,
            "缺陷数": len(d.cells),
            "描述": d.description,
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    # ── defect details ──
    st.subheader("缺陷详情")
    for i, d in enumerate(report.defects, 1):
        sev = _SEVERITY.get(d.severity, (d.severity, "❓"))
        typ = _DEFECT_TYPE.get(d.type, (d.type, "❓"))
        header = f"{sev[1]} #{i} [{sev[0]}] {typ[0]} — {d.sheet}"
        with st.expander(header, expanded=(d.severity == "critical" and i <= 3)):
            st.markdown(f"**Sheet**: {d.sheet}")
            st.markdown(f"**描述**: {d.description}")

            if d.cells:
                n = len(d.cells)
                st.markdown(f"**缺陷单元格** ({n} 个):")
                st.dataframe(
                    [{"单元格": c} for c in d.cells[:100]],
                    use_container_width=True, hide_index=True,
                )
                if n > 100:
                    st.caption(f"… 仅显示前 100 个，共 {n} 个")

            if d.context:
                st.markdown("**上下文**:")
                for k, v in d.context.items():
                    if isinstance(v, list) and len(v) > 20:
                        st.markdown(f"  - **{k}** ({len(v)} 项): {', '.join(str(x) for x in v[:10])} …")
                    else:
                        st.markdown(f"  - **{k}**: {v}")

    # ── auto-fix section ──
    fixable = [d for d in report.defects if d.type == "static_should_be_formula"]
    if fixable:
        st.divider()
        st.subheader("🔧 自动修复")
        st.markdown(
            f"检测到 **{len(fixable)}** 个可修复缺陷：将静态日期替换为 "
            "`MIN(DATE(YEAR(前一列),12,31), 结束日期)` 公式。"
            "修复使用 XML 级别操作，保留所有其他 sheet 的公式缓存值。"
        )
        if st.button("🔧 一键修复", type="primary", key="struct_fix"):
            src = st.session_state.get("diag_src_path")
            if not src or not Path(src).exists():
                st.error("源文件已过期，请重新上传。")
                return
            try:
                with st.spinner("正在修复（XML 级别操作）..."):
                    fixed_path = fix_structure_defects(src, fixable)
            except Exception as e:
                st.error(f"修复失败: {e}")
                return

            # Store fixed file bytes in session state for download
            src_name = st.session_state.get("diag_src_name", "output.xlsx")
            stem = Path(src_name).stem
            download_name = f"{stem}_fixed.xlsx"
            with open(fixed_path, "rb") as f:
                st.session_state["diag_fixed_bytes"] = f.read()
                st.session_state["diag_fixed_name"] = download_name
            Path(fixed_path).unlink(missing_ok=True)
            st.session_state["diag_fixed_count"] = sum(len(d.cells) for d in fixable)

    # Show download button if fix was done (persists across reruns)
    if "diag_fixed_bytes" in st.session_state:
        st.success(f"✅ 修复完成！已替换 {st.session_state.get('diag_fixed_count', 0)} 个静态单元格。")
        st.download_button(
            "📥 下载修复文件",
            data=st.session_state["diag_fixed_bytes"],
            file_name=st.session_state["diag_fixed_name"],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_fixed",
        )


def _render_structure_tab() -> None:
    st.markdown("扫描财务模型 Excel 文件，检测 SUMIF 块日期行中的结构缺陷。")
    st.caption("当日期行中首尾列是公式但中间列是静态值时，参数变化将无法完整传播。")

    file = st.file_uploader("上传 Excel 文件", type=["xlsx"], key="struct_file")
    if file:
        st.caption(f"📄 {file.name} ({file.size / 1024:.0f} KB)")

    # Clear stale results when a new file is uploaded
    if file and "diag_src_name" in st.session_state:
        if st.session_state.get("diag_src_name") != file.name:
            for key in list(st.session_state.keys()):
                if key.startswith("diag_"):
                    del st.session_state[key]

    if not file:
        st.info("请上传一个 Excel 文件以开始检查。")
        return

    # Run check button
    if st.button("🔍 开始检查", type="primary", key="struct_run"):
        src_path = _save_upload(file)
        st.session_state["diag_src_path"] = src_path
        st.session_state["diag_src_name"] = file.name
        # Clear previous fix
        for key in ("diag_fixed_bytes", "diag_fixed_name", "diag_fixed_count"):
            st.session_state.pop(key, None)

        try:
            with st.spinner("扫描 SUMIF 块与公式覆盖中..."):
                checker = ExcelStructureChecker()
                report = checker.check(src_path)
            st.session_state["diag_report"] = report
        except Exception as e:
            st.error(f"检查失败: {e}")
            return

    # Display results from session state (persists across reruns)
    if "diag_report" in st.session_state:
        st.divider()
        _render_defect_results(st.session_state["diag_report"])


# ── Main ───────────────────────────────────────────────────────────────────
tab_diff, tab_struct = st.tabs(["📊 差异根因分析", "🔎 结构缺陷检测"])

with tab_diff:
    _render_diff_tab()

with tab_struct:
    _render_structure_tab()
