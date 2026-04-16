import json
import os

def _serialize_page(page_data):
    """Internal helper to turn one page's elements into Markdown."""
    parts = []
    page_num = page_data.get('page_number')
    parts.append(f"\n### PAGE {page_num} ###\n")
    
    for elem in page_data.get('elements', []):
        e_type = elem.get('type')
        
        if e_type == 'text':
            content = elem.get('content', '').strip()
            if content:
                parts.append(content)
        
        elif e_type == 'table':
            parts.append("\n[EXTRACTED TABLE]")
            s_data = elem.get('structured_data', {})
            headers = s_data.get('headers', [])
            rows = s_data.get('rows', [])
            
            if headers or rows:
                md_table = []
                if headers:
                    md_table.append("| " + " | ".join(str(h) for h in headers) + " |")
                    md_table.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in rows:
                    md_table.append("| " + " | ".join(str(c) for c in row) + " |")
                parts.append("\n".join(md_table))
            parts.append("")

        elif e_type == 'image':
            desc = elem.get('vision_description', '').strip()
            if desc:
                parts.append(f"\n[IMAGE ANALYSIS]: {desc}\n")
    return "\n".join(parts)

def build_document_context(data_dict):
    """Serializes full extraction data dict into Markdown."""
    if not data_dict:
        return "No extracted data available."

    context_parts = []
    context_parts.append(f"DOCUMENT REPORT: {os.path.basename(data_dict.get('pdf', 'Unknown'))}")
    context_parts.append(f"TOTAL PAGES: {data_dict.get('total_pages', 0)}")
    context_parts.append("-" * 30)

    for page in data_dict.get('pages', []):
        context_parts.append(_serialize_page(page))

    return "\n".join(context_parts)

def build_page_context(data_dict, page_num):
    """Serializes only a specific page from extraction data dict into Markdown."""
    if not data_dict:
        return f"No extraction data for page {page_num}."

    for page in data_dict.get('pages', []):
        if page.get('page_number') == page_num:
            return _serialize_page(page)

    return f"Page {page_num} not found in extraction results."
