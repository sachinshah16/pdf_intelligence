import os
import fitz  # PyMuPDF
from PIL import Image, ImageDraw
import io
from collections import defaultdict

def sort_elements_by_reading_order(elements: list, line_tolerance: int = 15) -> list:
    """
    Advanced Column-Aware sorting for PDF elements.
    Identifies vertical columns and sorts within each column top-to-bottom.
    """
    if not elements:
        return []

    # 1. Identify Column Structure
    # We look for gaps in the horizontal (X) distribution of elements
    sorted_by_x = sorted(elements, key=lambda e: e.get("bbox_pixels", [0]*4)[0])
    
    columns = []
    if sorted_by_x:
        current_column = [sorted_by_x[0]]
        for i in range(1, len(sorted_by_x)):
            prev_bbox = sorted_by_x[i-1].get("bbox_pixels", [0]*4)
            curr_bbox = sorted_by_x[i].get("bbox_pixels", [0]*4)
            
            # If the current element starts significantly after the previous one ends,
            # it might be a new column. 
            # A 'significant' gap is typically > 10% of page width, but let's use 60px as a heuristic for 150DPI.
            gap = curr_bbox[0] - prev_bbox[2]
            if gap > 60: 
                columns.append(current_column)
                current_column = [sorted_by_x[i]]
            else:
                current_column.append(sorted_by_x[i])
        columns.append(current_column)

    # 2. Assign Column IDs and Sort
    # We sort each column internally by Y first
    final_sorted = []
    # Sort columns by their average X to ensure Left-to-Right
    columns.sort(key=lambda col: sum(e.get("bbox_pixels", [0]*4)[0] for e in col) / len(col))
    
    for col in columns:
        # Sort elements within this column top-to-bottom
        col_sorted = sorted(col, key=lambda e: e.get("bbox_pixels", [0]*4)[1])
        final_sorted.extend(col_sorted)

    return final_sorted


def find_repeated_regions(all_pages_elements: list,
                           repeat_threshold: int = 3,
                           y_tolerance: int = 15) -> set:
    """
    Scan all pages and find text elements that repeat at the same
    vertical position — these are headers/footers.
    """
    y_content_map = defaultdict(list)

    for page in all_pages_elements:
        for elem in page.get("elements", []):
            if elem.get("type") != "text":
                continue
            bbox = elem.get("bbox_pixels", [])
            if not bbox:
                continue
            content = elem.get("content", "").strip()
            if not content:
                continue
            snapped_y = (bbox[1] // y_tolerance) * y_tolerance
            y_content_map[snapped_y].append(content)

    repeated_y_positions = set()
    for snapped_y, contents in y_content_map.items():
        if len(contents) >= repeat_threshold:
            unique = set(c[:30] for c in contents)
            if len(unique) <= 2:
                repeated_y_positions.add(snapped_y)

    return repeated_y_positions

def is_header_or_footer(bbox: list, img_height: int,
                         header_margin: float = 0.07,
                         footer_margin: float = 0.07) -> bool:
    """
    Returns True if the element lies within the header or footer zone.
    """
    x1, y1, x2, y2 = bbox
    header_zone = img_height * header_margin
    footer_zone  = img_height * (1 - footer_margin)

    elem_center_y = (y1 + y2) / 2
    return elem_center_y < header_zone or elem_center_y > footer_zone

def pdf_page_to_image(pdf_path: str, page_num: int, dpi: int = 150) -> Image.Image:
    """Render a PDF page to a PIL Image."""
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    doc.close()
    return img

def crop_region(image: Image.Image, bbox: list, padding: int = 4) -> Image.Image:
    """Crop a region from a PIL image with optional padding."""
    x1, y1, x2, y2 = bbox
    w, h = image.size
    x1 = max(0, int(x1 - padding))
    y1 = max(0, int(y1 - padding))
    x2 = min(w, int(x2 + padding))
    y2 = min(h, int(y2 + padding))
    return image.crop((x1, y1, x2, y2))

def scale_bbox_to_pdf(bbox: list, img_size: tuple, page_size: tuple) -> tuple:
    """Scale image pixel bbox back to PDF coordinate space."""
    img_w, img_h = img_size
    pdf_w, pdf_h = page_size
    x1, y1, x2, y2 = bbox
    sx = pdf_w / img_w
    sy = pdf_h / img_h
    return (x1 * sx, y1 * sy, x2 * sx, y2 * sy)

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def is_contained(inner: list, outer: list, threshold: float = 0.85) -> bool:
    """Returns True if 'inner' box is substantially inside 'outer' box."""
    ix1, iy1, ix2, iy2 = inner
    ox1, oy1, ox2, oy2 = outer
    
    # Calculate area of intersection
    x_left = max(ix1, ox1)
    y_top = max(iy1, oy1)
    x_right = min(ix2, ox2)
    y_bottom = min(iy2, oy2)
    
    if x_right < x_left or y_bottom < y_top:
        return False
        
    intersection_area = (x_right - x_left) * (y_bottom - y_top)
    inner_area = (ix2 - ix1) * (iy2 - iy1)
    
    if inner_area <= 0: return False
    return (intersection_area / inner_area) >= threshold

def union_bboxes(bboxes: list) -> list:
    """Returns a single bounding box that contains all input bboxes."""
    if not bboxes: return []
    x1 = min(b[0] for b in bboxes)
    y1 = min(b[1] for b in bboxes)
    x2 = max(b[2] for b in bboxes)
    y2 = max(b[3] for b in bboxes)
    return [x1, y1, x2, y2]

def save_annotated_page(page_image: Image.Image, elements: list, output_path: str, page_num: int):
    """Draw bounding boxes on the page image for visualization."""
    draw = ImageDraw.Draw(page_image)
    
    colors = {
        "text": (0, 0, 255, 128),   # Blue
        "table": (0, 255, 0, 128),  # Green
        "image": (255, 0, 0, 128)   # Red
    }
    
    for elem in elements:
        bbox = elem.get("bbox_pixels")
        etype = elem.get("type", "text")
        color = colors.get(etype, (128, 128, 128, 128))
        
        if bbox:
            draw.rectangle(bbox, outline=color, width=3)
            draw.text((bbox[0], bbox[1] - 10), etype.upper(), fill=color)
            
    page_image.save(output_path)
