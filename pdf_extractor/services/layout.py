from paddleocr import LayoutDetection
import os
from .utils import is_contained, union_bboxes
import threading

# Labels PaddleOCR detects - adjust if your model returns different ones
TABLE_LABELS = {"table", "table_caption", "table_footnote"}
IMAGE_LABELS = {"figure", "figure_caption", "chart", "image"}
TEXT_LABELS  = {"text", "title", "header", "paragraph", "footer",
                "reference", "abstract", "section", "equation"}

# GLOBAL MODEL INITIALIZATION (Singleton Pattern)
_doc_layout_model = None
_model_lock = threading.Lock()

def get_layout_model():
    global _doc_layout_model
    with _model_lock:
        if _doc_layout_model is None:
            # Revert to the class your system supports
            from paddleocr import LayoutDetection
            _doc_layout_model = LayoutDetection(model_name="PP-DocLayout_plus-L", device="cpu")
    return _doc_layout_model

def detect_layout(image_path: str) -> list[dict]:
    """
    Detects document regions using PP-DocLayout.
    Returns: List of detected objects with type and bbox [x1, y1, x2, y2]
    """
    from PIL import Image
    with Image.open(image_path) as img:
        width, height = img.size

    model = get_layout_model()
    
    # PaddleOCR's underlying engine is not thread-safe for concurrent predict() calls.
    # We use a lock to ensure only one thread uses the model at a time.
    with _model_lock:
        results = model.predict(image_path, batch_size=1, layout_nms=True)

    regions = []
    if not results or "boxes" not in results[0]:
        return regions

    for item in results[0]["boxes"]:
        label = item["label"].lower().strip()
        coords = item["coordinate"] # [x1, y1, x2, y2]
        
        # Normalize to 0-1.0, explicitly casting to Python float
        # (PaddleOCR returns numpy float32 which is not JSON-serializable)
        norm_bbox = [
            float(coords[0]) / width,
            float(coords[1]) / height,
            float(coords[2]) / width,
            float(coords[3]) / height
        ]
        
        regions.append({
            "label": label,
            "bbox": norm_bbox,
            "score": round(float(item["score"]), 3),
        })
    return regions

def classify_region(label: str) -> str:
    """Returns 'text', 'table', or 'image'."""
    if label in TABLE_LABELS:
        return "table"
    elif label in IMAGE_LABELS:
        return "image"
    else:
        return "text"

def refine_regions(regions: list) -> list:
    """
    Apply geometric refinement:
    1. Filter out icons (small images < 50x50).
    2. Universal Containment: Filter out regions (text/image) inside tables or images.
    3. Group vertically adjacent text regions ONLY if they have the exact same label.
    """
    if not regions: return []

    # 1. Size & Identity Filter
    # Discard very small images (icons)
    initial_clean = []
    for r in regions:
        etype = classify_region(r["label"])
        if etype == "image":
            w = r["bbox"][2] - r["bbox"][0]
            h = r["bbox"][3] - r["bbox"][1]
            if w < 0.005 and h < 0.005: # Skip icon-sized snippets (< 0.5% of page)
                continue # Skip small icon
        initial_clean.append(r)

    # 2. Universal Containment Pass
    # Elements inside 'table' or 'image' belong to that container
    containers = [r for r in initial_clean if classify_region(r["label"]) in ("table", "image")]
    
    unique_regions = []
    for r in initial_clean:
        is_nested = False
        for c in containers:
            if r == c: continue # Don't compare with self
            if is_contained(r["bbox"], c["bbox"]):
                is_nested = True
                break
        if not is_nested:
            unique_regions.append(r)
    
    # 3. Group adjacent text regions (Strict label matching)
    # Sort vertically for grouping
    unique_regions.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
    
    grouped_result = []
    skipped_indices = set()
    
    for i in range(len(unique_regions)):
        if i in skipped_indices: continue
        
        current = unique_regions[i]
        curr_type = classify_region(current["label"])
        
        # Only attempt to merge 'text' types
        if curr_type != "text":
            grouped_result.append(current)
            continue
            
        group = [current]
        
        # Look ahead for mergeable blocks with SAME label
        for j in range(i+1, len(unique_regions)):
            if j in skipped_indices: continue
            next_r = unique_regions[j]
            next_type = classify_region(next_r["label"])
            
            # MUST be same type AND same label (e.g. paragraph vs paragraph)
            if next_type != "text" or next_r["label"] != current["label"]:
                break
                
            # Check vertical distance (0.5% of height threshold)
            v_gap_threshold = 0.005 # 15px at ~3000px height is ~0.5%
            v_gap = next_r["bbox"][1] - union_bboxes([item["bbox"] for item in group])[3]
            
            if v_gap < v_gap_threshold:
                # Check horizontal alignment (50% overlap)
                curr_box = union_bboxes([item["bbox"] for item in group])
                next_box = next_r["bbox"]
                
                h_overlap = min(curr_box[2], next_box[2]) - max(curr_box[0], next_box[0])
                min_w = min(curr_box[2]-curr_box[0], next_box[2]-next_box[0])
                
                if h_overlap > (0.5 * min_w):
                    group.append(next_r)
                    skipped_indices.add(j)
                else:
                    break
            else:
                break
        
        if len(group) > 1:
            merged_box = union_bboxes([item["bbox"] for item in group])
            current["bbox"] = merged_box
            current["score"] = round(sum(item["score"] for item in group) / len(group), 3)
            
        grouped_result.append(current)
    
    return grouped_result
