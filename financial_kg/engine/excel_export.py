"""全量 Excel 导出: 用 snapshot 值覆盖原始 Excel。"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, date
from typing import Any
from xml.etree import ElementTree as ET

SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = f"{{{SHEET_NS}}}"

# Match ISO datetime strings: "2023-12-31", "2023-12-31T00:00:00", "2023-12-31T00:00:00.000000"
_ISO_DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?)?$"
)

# Excel date epoch: 1899-12-30 (due to Lotus 1-2-3 bug)
_EXCEL_EPOCH = date(1899, 12, 30)


def _is_date_string(value: Any) -> bool:
    """Check if a value is an ISO date/datetime string."""
    if not isinstance(value, str):
        return False
    return _ISO_DATETIME_RE.match(value) is not None


def _sanitize_for_excel(value: Any) -> Any:
    """将 snapshot 中的值转换为 Excel 可识别的类型。

    - ISO 日期字符串 → datetime.date / datetime.datetime
    - 其他值保持原样
    """
    if isinstance(value, str):
        m = _ISO_DATETIME_RE.match(value)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if m.group(4):  # Has time component
                hour = int(m.group(4))
                minute = int(m.group(5))
                second = int(m.group(6))
                return datetime(year, month, day, hour, minute, second)
            return date(year, month, day)
    return value


def _value_to_excel_value(value: Any, is_date: bool = False) -> str:
    """将 Python 值转换为 Excel XML <v> 文本。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return str((value.date() - _EXCEL_EPOCH).days)
    if isinstance(value, date) and not isinstance(value, datetime):
        return str((value - _EXCEL_EPOCH).days)
    # Auto-detect ISO date strings regardless of is_date flag
    if isinstance(value, str):
        m = _ISO_DATETIME_RE.match(value)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return str((date(y, mo, d) - _EXCEL_EPOCH).days)
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            if abs(value) < 1e-6 and value != 0:
                return f"{value:.15g}"
            return f"{value:.15g}"
        return str(value)
    return str(value)


def _parse_cell_ref(ref: str) -> tuple[str, int]:
    """Parse 'F4' → ('F', 4)."""
    m = re.match(r"([A-Za-z]+)(\d+)", ref)
    if not m:
        return ("", 0)
    return m.group(1).upper(), int(m.group(2))


def export_parsed_excel(
    original_excel_path: str,
    graph_cells: dict[str, Any],
    output_path: str,
) -> str:
    """根据解析后的 graph.cells 导出 Excel。

    使用 zipfile/XML 直接操作，保留共享公式组和缓存值。
    只覆盖非公式的常量单元格，公式单元格完整保留（公式+缓存）。

    Args:
        original_excel_path: 原始 .xlsx 文件路径
        graph_cells: cell_id -> Cell 映射 (来自 FinancialGraph.cells)
        output_path: 输出文件路径

    Returns:
        output_path
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Step 1: copy original
    shutil.copy2(original_excel_path, output_path)

    # Step 2: build shared strings map + sheet name mapping
    shared_strings: list[str] = []
    sheet_name_map: dict[str, str] = {}  # sheet_path -> sheet_name
    with zipfile.ZipFile(output_path, "r") as z:
        if "xl/sharedStrings.xml" in z.namelist():
            ss_xml = z.read("xl/sharedStrings.xml")
            ss_root = ET.fromstring(ss_xml)
            for si in ss_root:
                # Use .iter() to find <t> at any depth (handles rich text <r><t> nesting)
                texts = list(si.iter(f"{NS}t"))
                text = "".join(t.text or "" for t in texts)
                shared_strings.append(text)

        # Map sheet XML paths to sheet names via xl/workbook.xml
        if "xl/workbook.xml" in z.namelist():
            wb_xml = z.read("xl/workbook.xml")
            wb_root = ET.fromstring(wb_xml)
            # Build rId -> sheet_name
            rid_to_name: dict[str, str] = {}
            for sheet in wb_root.findall(f"{NS}sheets/{NS}sheet"):
                rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                name = sheet.get("name")
                if rid and name:
                    rid_to_name[rid] = name
            # Build sheet_path -> sheet_name via xl/_rels/workbook.xml.rels
            if "xl/_rels/workbook.xml.rels" in z.namelist():
                rels_xml = z.read("xl/_rels/workbook.xml.rels")
                rels_root = ET.fromstring(rels_xml)
                rid_to_target: dict[str, str] = {}
                for rel in rels_root:
                    rid = rel.get("Id")
                    target = rel.get("Target")
                    if rid and target:
                        # Target is like "worksheets/sheet1.xml"
                        rid_to_target[rid] = target
                for rid, target in rid_to_target.items():
                    if rid in rid_to_name:
                        full_path = f"xl/{target}"
                        sheet_name_map[full_path] = rid_to_name[rid]

    # Build reverse map: string -> index
    str_to_index: dict[str, int] = {}
    for idx, s in enumerate(shared_strings):
        str_to_index[s] = idx

    # Step 3: modify sheet XMLs (collect in memory first, then write after zip closes)
    modified_sheets: dict[str, bytes] = {}  # sheet_path -> new XML bytes
    with zipfile.ZipFile(output_path, "r") as z:
        sheet_files = [f for f in z.namelist() if f.startswith("xl/worksheets/sheet")]
        has_shared_strings = "xl/sharedStrings.xml" in z.namelist()

        for sheet_path in sheet_files:
            sheet_xml = z.read(sheet_path)
            root = ET.fromstring(sheet_xml)

            sheet_name = sheet_name_map.get(sheet_path)
            if not sheet_name:
                continue

            modified = False
            for cell in root.iter(f"{NS}c"):
                cell_ref = cell.get("r", "")
                if not cell_ref:
                    continue

                col, row = _parse_cell_ref(cell_ref)
                if not col:
                    continue

                cell_id = f"{sheet_name}_{row}_{col}"
                graph_cell = graph_cells.get(cell_id)
                if graph_cell is None:
                    continue

                # Skip formula cells — preserve both formula and cached value
                formula_el = cell.find(f"{NS}f")
                if formula_el is not None:
                    continue

                # Skip if value unchanged
                current_v = cell.find(f"{NS}v")
                current_text = current_v.text if current_v is not None else None
                new_raw = graph_cell.value

                # Compare: need to handle type differences
                if current_text is not None:
                    old_val = _try_parse_cell_value(cell, current_text, shared_strings)
                    if old_val == new_raw:
                        continue

                # Update cell value
                new_text = _value_to_excel_value(new_raw, is_date=(graph_cell.data_type == "date"))
                is_date_str = _is_date_string(new_raw)

                t_attr = cell.get("t", "n")

                if t_attr == "s" and isinstance(new_raw, str) and not is_date_str:
                    # Shared string — update sharedStrings.xml and cell index
                    if new_raw not in str_to_index:
                        str_to_index[new_raw] = len(shared_strings)
                        shared_strings.append(new_raw)
                    cell.set("t", "s")
                    v_el = cell.find(f"{NS}v")
                    if v_el is None:
                        v_el = ET.SubElement(cell, f"{NS}v")
                    v_el.text = str(str_to_index[new_raw])
                elif t_attr == "inlineStr" and not is_date_str:
                    # Inline string — use <is><t>...</t></is>
                    cell.set("t", "inlineStr")
                    if current_v is not None:
                        cell.remove(current_v)
                    is_el = cell.find(f"{NS}is")
                    if is_el is None:
                        is_el = ET.SubElement(cell, f"{NS}is")
                    t_el = is_el.find(f"{NS}t")
                    if t_el is None:
                        t_el = ET.SubElement(is_el, f"{NS}t")
                    t_el.text = new_raw if new_raw is not None else ""
                elif isinstance(new_raw, str) and not is_date_str:
                    # String without explicit type — use inlineStr
                    cell.set("t", "inlineStr")
                    if current_v is not None:
                        cell.remove(current_v)
                    is_el = cell.find(f"{NS}is")
                    if is_el is None:
                        is_el = ET.SubElement(cell, f"{NS}is")
                    t_el = is_el.find(f"{NS}t")
                    if t_el is None:
                        t_el = ET.SubElement(is_el, f"{NS}t")
                    t_el.text = new_raw
                else:
                    # Number or date — update <v>
                    cell.set("t", "n")
                    if current_v is None:
                        v_el = ET.SubElement(cell, f"{NS}v")
                        v_el.text = new_text
                        # Move <v> before <f>
                        children = list(cell)
                        if len(children) > 1:
                            cell.remove(v_el)
                            cell.insert(0, v_el)
                    else:
                        current_v.text = new_text

                modified = True

            if modified:
                # Write modified sheet XML (no indent to preserve original formatting)
                new_sheet_xml = ET.tostring(root, encoding="UTF-8", xml_declaration=False)
                modified_sheets[sheet_path] = new_sheet_xml

    # Step 4: apply all replacements (zip is closed now)
    for sheet_path, new_sheet_xml in modified_sheets.items():
        _replace_in_zip(output_path, sheet_path, new_sheet_xml)

    if has_shared_strings:
        _update_shared_strings(output_path, shared_strings)

    # Step 5: force full recalculation on open so modified values propagate to formula cells
    _set_full_calc_on_load(output_path)

    return output_path


def _set_full_calc_on_load(xlsx_path: str) -> None:
    """Set workbook to force full recalculation on next open."""
    tmp_path = xlsx_path + ".tmp2"
    with zipfile.ZipFile(xlsx_path, "r") as zin:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "xl/workbook.xml":
                    wb_xml = zin.read(item.filename)
                    root = ET.fromstring(wb_xml)
                    calc_pr = root.find(f"{NS}calcPr")
                    if calc_pr is None:
                        calc_pr = ET.SubElement(root, f"{NS}calcPr")
                    calc_pr.set("calcMode", "auto")
                    calc_pr.set("fullCalcOnLoad", "1")
                    new_xml = ET.tostring(root, encoding="UTF-8", xml_declaration=False)
                    zout.writestr(item.filename, new_xml, compress_type=item.compress_type)
                else:
                    zout.writestr(item.filename, zin.read(item.filename), compress_type=item.compress_type)
    os.replace(tmp_path, xlsx_path)


def _try_parse_cell_value(cell: ET.Element, text: str, shared_strings: list[str] | None = None) -> Any:
    """Try to parse a cell's <v> text back to a Python value for comparison."""
    t_attr = cell.get("t", "n")
    if t_attr == "n":
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return text
    if t_attr == "s":
        # Resolve shared string index to actual text
        if shared_strings is not None:
            try:
                idx = int(text)
                if 0 <= idx < len(shared_strings):
                    return shared_strings[idx]
            except ValueError:
                pass
        return text  # Fallback: return index as-is
    if t_attr == "b":
        return text == "1"
    return text


def _update_shared_strings(xlsx_path: str, strings: list[str]) -> None:
    """Update sharedStrings.xml in the xlsx file."""
    ss_root = ET.Element(f"{NS}sst")
    ss_root.set("xmlns", SHEET_NS)
    ss_root.set("uniqueCount", str(len(strings)))

    for s in strings:
        si = ET.SubElement(ss_root, f"{NS}si")
        t = ET.SubElement(si, f"{NS}t")
        t.text = s

    # Pretty print (Excel doesn't need it but helps debugging)
    ET.indent(ss_root, space="", level=0)
    ss_xml = ET.tostring(ss_root, encoding="UTF-8", xml_declaration=True)

    _replace_in_zip(xlsx_path, "xl/sharedStrings.xml", ss_xml)


def _replace_in_zip(xlsx_path: str, internal_path: str, new_content: bytes) -> None:
    """Replace a file inside a zip/xlsx in-place."""
    dir_name = os.path.dirname(os.path.abspath(xlsx_path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".xlsx")
    os.close(fd)
    try:
        with zipfile.ZipFile(xlsx_path, "r") as zin:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename == internal_path:
                        zout.writestr(item.filename, new_content, compress_type=item.compress_type)
                    else:
                        zout.writestr(item.filename, zin.read(item.filename), compress_type=item.compress_type)
        os.replace(tmp_path, xlsx_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def export_modified_excel(
    original_excel_path: str,
    snapshot_values: dict[str, Any],
    output_path: str,
    *,
    formula_cell_ids: set[str] | None = None,
) -> str:
    """加载原始 Excel, 用 snapshot 值覆盖对应单元格, 保留公式缓存/共享公式组。

    使用 zipfile/XML 直接操作，保留共享公式组和缓存值。
    只覆盖非公式的常量单元格，公式单元格完整保留。

    Args:
        original_excel_path: 原始 .xlsx 文件路径
        snapshot_values: cell_id -> value 映射
        output_path: 输出文件路径
        formula_cell_ids: 公式单元格 ID 集合。传入时跳过这些单元格, 保留原公式。

    Returns:
        output_path
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    shutil.copy2(original_excel_path, output_path)

    # Step 1: build shared strings map + sheet name mapping
    shared_strings: list[str] = []
    sheet_name_map: dict[str, str] = {}
    with zipfile.ZipFile(output_path, "r") as z:
        if "xl/sharedStrings.xml" in z.namelist():
            ss_xml = z.read("xl/sharedStrings.xml")
            ss_root = ET.fromstring(ss_xml)
            for si in ss_root:
                # Use .iter() to find <t> at any depth (handles rich text <r><t> nesting)
                texts = list(si.iter(f"{NS}t"))
                text = "".join(t.text or "" for t in texts)
                shared_strings.append(text)

        if "xl/workbook.xml" in z.namelist():
            wb_xml = z.read("xl/workbook.xml")
            wb_root = ET.fromstring(wb_xml)
            rid_to_name: dict[str, str] = {}
            for sheet in wb_root.findall(f"{NS}sheets/{NS}sheet"):
                rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                name = sheet.get("name")
                if rid and name:
                    rid_to_name[rid] = name
            if "xl/_rels/workbook.xml.rels" in z.namelist():
                rels_xml = z.read("xl/_rels/workbook.xml.rels")
                rels_root = ET.fromstring(rels_xml)
                rid_to_target: dict[str, str] = {}
                for rel in rels_root:
                    rid = rel.get("Id")
                    target = rel.get("Target")
                    if rid and target:
                        rid_to_target[rid] = target
                for rid, target in rid_to_target.items():
                    if rid in rid_to_name:
                        full_path = f"xl/{target}"
                        sheet_name_map[full_path] = rid_to_name[rid]

    str_to_index: dict[str, int] = {}
    for idx, s in enumerate(shared_strings):
        str_to_index[s] = idx

    # Step 2: modify sheet XMLs
    modified_sheets: dict[str, bytes] = {}
    with zipfile.ZipFile(output_path, "r") as z:
        sheet_files = [f for f in z.namelist() if f.startswith("xl/worksheets/sheet")]

        for sheet_path in sheet_files:
            sheet_xml = z.read(sheet_path)
            root = ET.fromstring(sheet_xml)

            sheet_name = sheet_name_map.get(sheet_path)
            if not sheet_name:
                continue

            modified = False
            for cell in root.iter(f"{NS}c"):
                cell_ref = cell.get("r", "")
                if not cell_ref:
                    continue

                col, row = _parse_cell_ref(cell_ref)
                if not col:
                    continue

                cell_id = f"{sheet_name}_{row}_{col}"
                if cell_id not in snapshot_values:
                    continue

                # Skip formula cells
                formula_el = cell.find(f"{NS}f")
                if formula_el is not None:
                    if formula_cell_ids and cell_id in formula_cell_ids:
                        continue
                    # If no formula_cell_ids provided but cell has formula, skip anyway
                    continue

                new_raw = snapshot_values[cell_id]
                current_v = cell.find(f"{NS}v")
                current_text = current_v.text if current_v is not None else None

                if current_text is not None:
                    old_val = _try_parse_cell_value(cell, current_text, shared_strings)
                    if old_val == new_raw:
                        continue

                new_text = _value_to_excel_value(new_raw)
                is_date_str = _is_date_string(new_raw)

                t_attr = cell.get("t", "n")

                if t_attr == "s" and isinstance(new_raw, str) and not is_date_str:
                    if new_raw not in str_to_index:
                        str_to_index[new_raw] = len(shared_strings)
                        shared_strings.append(new_raw)
                    cell.set("t", "s")
                    v_el = cell.find(f"{NS}v")
                    if v_el is None:
                        v_el = ET.SubElement(cell, f"{NS}v")
                    v_el.text = str(str_to_index[new_raw])
                elif t_attr == "inlineStr" and not is_date_str:
                    cell.set("t", "inlineStr")
                    if current_v is not None:
                        cell.remove(current_v)
                    is_el = cell.find(f"{NS}is")
                    if is_el is None:
                        is_el = ET.SubElement(cell, f"{NS}is")
                    t_el = is_el.find(f"{NS}t")
                    if t_el is None:
                        t_el = ET.SubElement(is_el, f"{NS}t")
                    t_el.text = new_raw if new_raw is not None else ""
                elif isinstance(new_raw, str) and not is_date_str:
                    cell.set("t", "inlineStr")
                    if current_v is not None:
                        cell.remove(current_v)
                    is_el = cell.find(f"{NS}is")
                    if is_el is None:
                        is_el = ET.SubElement(cell, f"{NS}is")
                    t_el = is_el.find(f"{NS}t")
                    if t_el is None:
                        t_el = ET.SubElement(is_el, f"{NS}t")
                    t_el.text = new_raw
                else:
                    cell.set("t", "n")
                    if current_v is None:
                        v_el = ET.SubElement(cell, f"{NS}v")
                        v_el.text = new_text
                        children = list(cell)
                        if len(children) > 1:
                            cell.remove(v_el)
                            cell.insert(0, v_el)
                    else:
                        current_v.text = new_text

                modified = True

            if modified:
                new_sheet_xml = ET.tostring(root, encoding="UTF-8", xml_declaration=False)
                modified_sheets[sheet_path] = new_sheet_xml

    # Step 3: apply all replacements
    for sheet_path, new_sheet_xml in modified_sheets.items():
        _replace_in_zip(output_path, sheet_path, new_sheet_xml)

    # Force full recalculation on open so modified values propagate to formula cells
    _set_full_calc_on_load(output_path)

    return output_path


def find_original_excel(task_id: str, output_dir: str) -> str | None:
    """查找原始 Excel 文件。

    优先查找 output/{task_id}_original.xlsx, 否则查找 output/ 下唯一的 .xlsx 文件。
    """
    candidate = os.path.join(output_dir, f"{task_id}_original.xlsx")
    if os.path.exists(candidate):
        return candidate

    xlsx_files = [f for f in os.listdir(output_dir) if f.endswith(".xlsx")]
    if len(xlsx_files) == 1:
        return os.path.join(output_dir, xlsx_files[0])

    return None
