"""Create swap-task Excel files from the validated workbook template."""
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import os
import xml.etree.ElementTree as ET


SHEET_XML = {
    "主图数据": "xl/worksheets/sheet1.xml",
    "替换数据": "xl/worksheets/sheet2.xml",
}
FIELDS = ("store_name", "product_id", "image_url", "product_code", "operator")
BODY_STYLES = ("14", "15", "16", "15", "15")
XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = {"x": XML_NS}
ET.register_namespace("x", XML_NS)


def _replace_sheet_rows(xml_bytes, records):
    root = ET.fromstring(xml_bytes)
    sheet_data = root.find("x:sheetData", NS)
    if sheet_data is None:
        raise ValueError("Excel template is missing sheetData")

    rows = list(sheet_data.findall("x:row", NS))
    for row in rows[1:]:
        sheet_data.remove(row)

    for row_number, record in enumerate(records, start=2):
        row = ET.SubElement(
            sheet_data,
            f"{{{XML_NS}}}row",
            {"r": str(row_number), "ht": "21", "customHeight": "1"},
        )
        for column_index, (field, style_id) in enumerate(zip(FIELDS, BODY_STYLES)):
            column = chr(ord("A") + column_index)
            cell = ET.SubElement(
                row,
                f"{{{XML_NS}}}c",
                {"r": f"{column}{row_number}", "s": style_id, "t": "inlineStr"},
            )
            value = "" if record.get(field) is None else str(record.get(field))
            if value:
                inline_string = ET.SubElement(cell, f"{{{XML_NS}}}is")
                ET.SubElement(inline_string, f"{{{XML_NS}}}t").text = value

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_swap_workbook(main_rows, replacement_rows, output_path, template_path=None):
    base_dir = Path(__file__).resolve().parent
    template = Path(template_path or base_dir / "assets" / "swap_task_template.xlsx")
    output = Path(output_path)
    if not template.exists():
        raise FileNotFoundError(f"Excel template not found: {template}")

    replacements = {
        SHEET_XML["主图数据"]: list(main_rows),
        SHEET_XML["替换数据"]: list(replacement_rows),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(output.suffix + ".tmp")

    with ZipFile(template, "r") as source:
        with ZipFile(temp_output, "w", ZIP_DEFLATED) as target:
            for item in source.infolist():
                content = source.read(item.filename)
                if item.filename in replacements:
                    content = _replace_sheet_rows(content, replacements[item.filename])
                target.writestr(item, content)

    os.replace(temp_output, output)
    return str(output)
