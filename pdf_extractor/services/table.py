import os
import time
import json
import re
from PIL import Image
import io

from .google_ai import extract_table_gemma

def extract_table_data(pdf_path, page_num, pdf_bbox, table_image_path):
    """
    Extract table data as JSON and generate local HTML for UI compatibility.
    """
    t0 = time.time()
    model_name = "gemini-2.5-flash" # Current priority for tables
    fname = os.path.basename(table_image_path)

    print(f"  [PAGE {page_num} | TABLE] Starting extraction: {fname}... (Model: {model_name})")

    try:
        # Load image as bytes
        with open(table_image_path, "rb") as f:
            image_bytes = f.read()

        # Call Gemma for JSON
        json_response, tokens, used_model = extract_table_gemma(image_bytes)
        
        if not json_response:
            print(f"  [PAGE {page_num} | TABLE] FAILED: {used_model} returned no content.")
            return _empty_result("gemma_error")

        # Parse JSON
        try:
            # Strip markdown code blocks if model included them
            clean_json = re.sub(r'```json\s*|\s*```', '', json_response).strip()
            
            # SANITIZE: Double-escape backslashes that AI often misses
            clean_json = re.sub(r'\\(?![\\\"\/bfnrtu])', r'\\\\', clean_json)

            data = json.loads(clean_json)
            headers = data.get("headers", [])
            rows = data.get("rows", [])
        except Exception as json_err:
            print(f"  [PAGE {page_num} | TABLE] JSON Parse Error: {json_err}")
            return _empty_result("parse_error")

        # Generate Local HTML for UI compatibility (Dashboard/Word)
        html_table = _generate_html_table(headers, rows)

        elapsed = time.time() - t0
        print(f"  [PAGE {page_num} | TABLE] Completed in {elapsed:.2f}s | {len(rows)} rows | Tokens: {tokens} | Model: {used_model}")

        return {
            "html": html_table,
            "headers": headers,
            "rows": rows,
            "method": f"{used_model}-json",
            "token_usage": tokens,
        }

    except Exception as e:
        print(f"  [TABLE] Failed: {e}")
        import traceback
        traceback.print_exc()
        return _empty_result("error")

def _generate_html_table(headers, rows):
    """Generate a clean, skeleton HTML table without inline styles."""
    html = ['<table>']
    
    if headers:
        html.append("<thead><tr>")
        for h in headers:
            html.append(f"<th>{h}</th>")
        html.append("</tr></thead>")
    
    if rows:
        html.append("<tbody>")
        for row in rows:
            html.append("<tr>")
            for cell in row:
                html.append(f"<td>{cell}</td>")
            html.append("</tr>")
        html.append("</tbody>")
        
    html.append("</table>")
    return "".join(html)

def _empty_result(method: str) -> dict:
    return {
        "html": "",
        "headers": [],
        "rows": [],
        "method": method,
    }
