#!/usr/bin/env python3
"""Minimal OpenDocument Spreadsheet reader -> text dump.

Handles repeated rows/columns, which LibreOffice emits heavily, so the dumped
grid keeps the true column alignment.
"""
import sys, zipfile, re
import xml.etree.ElementTree as ET

NS = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
}


def cell_text(c):
    parts = []
    for p in c.iter():
        tag = p.tag.split('}')[-1]
        if tag == 'p':
            t = ''.join(p.itertext())
            if t.strip():
                parts.append(t.strip())
        elif tag == 'image':
            parts.append('[IMAGE]')
    return '\n'.join(parts).strip()


def read(path, max_rows=400):
    with zipfile.ZipFile(path) as z:
        xml = z.read('content.xml')
    root = ET.fromstring(xml)
    body = root.find('office:body', NS)
    out = []
    for sheet in body.iter('{%s}table' % NS['table']):
        name = sheet.get('{%s}name' % NS['table'])
        out.append(('SHEET', name))
        rows = []
        for row in sheet.findall('table:table-row', NS):
            rep_r = int(row.get('{%s}number-rows-repeated' % NS['table'], 1))
            cells = []
            for cell in list(row):
                tag = cell.tag.split('}')[-1]
                if tag not in ('table-cell', 'covered-table-cell'):
                    continue
                rep = int(cell.get('{%s}number-columns-repeated' % NS['table'], 1))
                txt = cell_text(cell)
                for _ in range(min(rep, 64)):
                    cells.append(txt)
            while cells and not cells[-1]:
                cells.pop()
            if not cells:
                continue
            for _ in range(min(rep_r, 8)):
                rows.append(cells)
        out.append(('ROWS', rows[:max_rows]))
    return out


def dump(path, max_rows=400):
    print('#' * 78)
    print('# FILE:', path)
    print('#' * 78)
    for kind, payload in read(path, max_rows):
        if kind == 'SHEET':
            print('\n===== SHEET: %s =====' % payload)
            continue
        rows = payload
        print('(%d rows shown)' % len(rows))
        for i, cells in enumerate(rows, 1):
            while len(cells) > 1 and not cells[-1]:
                cells.pop()
            if not any(c.strip() for c in cells):
                continue
            parts = []
            for j, c in enumerate(cells):
                c = c.replace('\n', ' / ').strip()
                if c:
                    parts.append('[%d]%s' % (j + 1, c))
            if parts:
                print('%4d| %s' % (i, '  '.join(parts)))


if __name__ == '__main__':
    for p in sys.argv[1:]:
        dump(p)
        print()
