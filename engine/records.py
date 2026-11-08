"""Count records: one CSV is the truth, ODS and XLSX are generated from it.

The user asked for a spreadsheet that is updated on every saved count, with an
ODS plus backup XLSX and CSV. Writing three files independently means they will
eventually disagree, and a field record that disagrees with itself is worthless.
So: **CSV is authoritative and append-only**; ODS and XLSX are rebuilt from it
after every append. Either can be regenerated from the other at any time, and
the CSV stays readable in any spreadsheet, text editor or script.

ODS and XLSX are both ZIP containers of XML, so both are written here with the
standard library only - no dependency on a spreadsheet library.
"""

from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import datetime, timezone

# Column order. The first three are the forester's own; the rest exist so a
# count can be audited later against its photo, its scale and its calibration.
COLUMNS = [
    "Age_Name",              # text, 17 chars, e.g. XGV237P2T6
    "Ring_Count",            # integer, 3 digits
    "Confidence_+/-(yrs)",   # integer, 2 digits
    "Status",                # counted | partial | unresolved
    "Segment",               # segment id for a partial count on a broken core
    "Photo",                 # proof image filename
    "Record_JSON",           # sidecar with the ring geometry
    "Ring_Width_Median_mm",
    "Seg_From_mm",           # span start, from the named reference end
    "Seg_To_mm",
    "Px_per_mm",
    "N_Photos",              # photos combined into this count
    "Count_Min",             # so +/- is auditable, not merely asserted
    "Count_Max",
    "Source_Image",
    "Recorded_UTC",
]

INT_COLUMNS = {"Ring_Count", "Confidence_+/-(yrs)", "N_Photos",
               "Count_Min", "Count_Max"}


def _esc(v: str) -> str:
    """XML-escape for the hand-written ODS/XLSX parts."""
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _clip(value, width: int) -> str:
    s = "" if value is None else str(value)
    return s[:width]


def _norm(row: dict) -> list[str]:
    out = []
    for c in COLUMNS:
        v = row.get(c)
        if c in INT_COLUMNS and v not in (None, ""):
            try:
                v = int(round(float(v)))
            except (TypeError, ValueError):
                v = 0
        out.append(_clip(v, 17) if c == "Age_Name" else _clip(v, 255))
    return out


def record_from_count(age_name: str, count: int, confidence, *,
                      status: str = "counted", segment: str = "",
                      proof_png: str = "", record_json: str = "",
                      width_median_mm=None, seg_from_mm=None,
                      seg_to_mm=None, px_per_mm=None, n_photos: int = 1,
                      count_min=None, count_max=None,
                      source_image: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if count_min is None:
        count_min = count
    if count_max is None:
        count_max = count
    return {
        "Age_Name": age_name[:17],
        "Ring_Count": int(count),
        "Confidence_+/-(yrs)": confidence,
        "Status": status,
        "Segment": segment,
        "Photo": os.path.basename(proof_png) if proof_png else "",
        "Record_JSON": os.path.basename(record_json) if record_json else "",
        "Ring_Width_Median_mm": (round(float(width_median_mm), 4)
                                 if width_median_mm else ""),
        "Seg_From_mm": seg_from_mm if seg_from_mm is not None else "",
        "Seg_To_mm": seg_to_mm if seg_to_mm is not None else "",
        "Px_per_mm": round(float(px_per_mm), 2) if px_per_mm else "",
        "N_Photos": int(n_photos),
        "Count_Min": int(count_min),
        "Count_Max": int(count_max),
        "Source_Image": os.path.basename(source_image) if source_image else "",
        "Recorded_UTC": now,
    }


# --- CSV -------------------------------------------------------------------

def write_csv(rows: list[dict], path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if not exists:
            w.writerow(COLUMNS)
        for r in rows:
            w.writerow(_norm(r))
    return path


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- ODS (OpenDocument spreadsheet) ----------------------------------------

def write_ods(rows: list[dict], path: str) -> str:
    cells = []
    header = "".join(
        f'<table:table-cell office:value-type="string">'
        f'<text:p>{_esc(c)}</text:p></table:table-cell>' for c in COLUMNS)
    cells.append(f"<table:table-row>{header}</table:table-row>")
    for r in rows:
        vals = _norm(r)
        tds = []
        for c, v in zip(COLUMNS, vals):
            if c in INT_COLUMNS and v not in ("", None):
                try:
                    tds.append(f'<table:table-cell office:value-type="float" '
                               f'office:value="{int(round(float(v)))}">'
                               f'<text:p>{int(round(float(v)))}</text:p>'
                               f'</table:table-cell>')
                    continue
                except ValueError:
                    pass
            tds.append(f'<table:table-cell office:value-type="string">'
                       f'<text:p>{_esc(v)}</text:p></table:table-cell>')
        cells.append(f"<table:table-row>{''.join(tds)}</table:table-row>")

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'office:version="1.2"><office:body><office:spreadsheet>'
        '<table:table table:name="counts">'
        + "".join(cells) +
        '</table:table></office:spreadsheet></office:body></office:document-content>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document-styles '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'office:version="1.2"><office:styles/></office:document-styles>'
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<manifest:manifest '
        'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
        '<manifest:file-entry manifest:full-path="content.xml" '
        'manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" '
        'manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", manifest)
        z.writestr("styles.xml", styles)
        z.writestr("content.xml", content)
    return path


# --- XLSX ------------------------------------------------------------------

def write_xlsx(rows: list[dict], path: str) -> str:
    def cell(ref: str, v: str, numeric: bool) -> str:
        if numeric and v not in ("", None):
            try:
                return f'<c r="{ref}"><v>{int(round(float(v)))}</v></c>'
            except ValueError:
                pass
        return (f'<c r="{ref}" t="inlineStr"><is><t>{_esc(v)}</t></is></c>')

    def colname(i: int) -> str:
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    xml_rows = []
    hdr = "".join(cell(f"{colname(i)}1", c, False)
                  for i, c in enumerate(COLUMNS))
    xml_rows.append(f'<row r="1">{hdr}</row>')
    for n, r in enumerate(rows, start=2):
        vals = _norm(r)
        tds = "".join(cell(f"{colname(i)}{n}", v, c in INT_COLUMNS)
                      for i, (c, v) in enumerate(zip(COLUMNS, vals)))
        xml_rows.append(f'<row r="{n}">{tds}</row>')

    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main"><sheetData>'
             + "".join(xml_rows) + '</sheetData></worksheet>')
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/'
          'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org'
          '/officeDocument/2006/relationships"><sheets>'
          '<sheet name="counts" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/'
                 'package/2006/relationships"><Relationship Id="rId1" Type='
                 '"http://schemas.openxmlformats.org/officeDocument/2006/'
                 'relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/'
              'spreadsheetml/2006/main"/>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="rels" ContentType="application/'
        'vnd.openxmlformats-package.relationships+xml"/><Default Extension='
        '"xml" ContentType="application/xml"/><Override PartName="/xl/'
        'workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/><Override PartName='
        '"/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


# --- the one call the app makes -------------------------------------------

class CountStore:
    """The AGE_COUNT folder: proof images, JSON sidecars, and the spreadsheet.

    CSV is the single source of truth; ODS and XLSX are rebuilt from it on every
    save so the three can never disagree.
    """

    def __init__(self, folder: str, basename: str = "counts"):
        self.folder = os.path.abspath(folder)
        self.csv = os.path.join(self.folder, f"{basename}.csv")
        self.ods = os.path.join(self.folder, f"{basename}.ods")
        self.xlsx = os.path.join(self.folder, f"{basename}.xlsx")
        os.makedirs(self.folder, exist_ok=True)

    def add(self, row: dict) -> dict:
        write_csv([row], self.csv)
        all_rows = read_csv(self.csv)
        write_ods(all_rows, self.ods)
        write_xlsx(all_rows, self.xlsx)
        return {"csv": self.csv, "ods": self.ods, "xlsx": self.xlsx,
                "rows": len(all_rows)}

    def proof_path(self, age_name: str, ext: str = ".png") -> str:
        return os.path.join(self.folder, f"{age_name}{ext}")

    def rows(self) -> list[dict]:
        return read_csv(self.csv)
