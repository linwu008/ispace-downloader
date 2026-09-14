"""Bounded text extraction. No macros, scripts or embedded objects are executed."""
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import re
import xml.etree.ElementTree as ET

MAX_TEXT = 180000


def extract(path, data=None):
    path = Path(path)
    parts = []
    remaining = MAX_TEXT

    def add(locator, text):
        nonlocal remaining
        text = text.strip()[:remaining]
        if text:
            parts.append({'locator': locator, 'text': text})
            remaining -= len(text)

    try:
        if path.suffix.lower() == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data) if data is not None else path)
            for n, page in enumerate(reader.pages):
                if not remaining:
                    break
                add(f'第 {n+1} 页', page.extract_text() or '')
        elif path.suffix.lower() in {'.pptx', '.docx'}:
            with ZipFile(BytesIO(data) if data is not None else path) as archive:
                names = ['word/document.xml'] if path.suffix.lower() == '.docx' else sorted(
                    (x for x in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', x)),
                    key=lambda x: int(re.search(r'slide(\d+)', x)[1]))
                for n, name in enumerate(names):
                    if not remaining:
                        break
                    if archive.getinfo(name).file_size > 10000000:
                        return []
                    tree = ET.fromstring(archive.read(name))
                    add(f'幻灯片 {n+1}' if path.suffix.lower() == '.pptx' else '文档正文', '\n'.join(x.text or '' for x in tree.iter() if x.tag.endswith('}t')))
        elif path.suffix.lower() in {'.txt', '.md', '.csv'}:
            if data is not None:
                add('正文', data.decode('utf-8', errors='replace')[:MAX_TEXT])
            else:
                with path.open('r', encoding='utf-8', errors='replace') as stream:
                    add('正文', stream.read(MAX_TEXT))
    except Exception:
        return []
    if remaining == 0 and parts:
        parts[0]['truncated'] = True
    return parts
