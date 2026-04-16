import os
import json
import time  # For timing
import threading
from pathlib import Path
from PIL import Image
import fitz  # PyMuPDF
from concurrent.futures import ThreadPoolExecutor

from .utils import (
    pdf_page_to_image, crop_region, scale_bbox_to_pdf, 
    ensure_dir, sort_elements_by_reading_order, 
    find_repeated_regions, is_header_or_footer,
    save_annotated_page
)
from .layout import detect_layout, classify_region, refine_regions
from .text import extract_text_from_bbox
from .table import extract_table_data
from .vision import describe_image_moondream, batch_describe_images
from .report import generate_word_report

def process_pdf_pipeline(pdf_path: str, output_dir: str = "output/pdf_extractor", dpi: int = 150):
    """
    Main PDF extraction pipeline orchestrator with detailed timing.
    """
    start_total = time.time()
    total_tokens = 0
    pdf_name = Path(pdf_path).stem
    work_dir = os.path.join(output_dir, pdf_name)
    pages_dir = os.path.join(work_dir, "pages")
    tables_dir = os.path.join(work_dir, "tables")
    images_dir = os.path.join(work_dir, "images")
    annotated_dir = os.path.join(work_dir, "annotated")

    ensure_dir(pages_dir)
    ensure_dir(tables_dir)
    ensure_dir(images_dir)
    ensure_dir(annotated_dir)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    full_output = {
        "pdf": pdf_path,
        "total_pages": total_pages,
        "pages": []
    }

    # Thread-safe counters and results
    total_tokens_lock = threading.Lock()
    nonlocal_total_tokens = 0
    nonlocal_total_requests = 0

    def process_single_page(page_num):
        nonlocal nonlocal_total_tokens, nonlocal_total_requests
        print(f"\n--- [START] Processing Page {page_num + 1}/{total_pages} ---")
        start_page = time.time()

        # 1. Rendering (Low-DPI) for Fast Layout Detection
        t0 = time.time()
        # 72 DPI is enough for layout models to see block boundaries
        page_image_low = pdf_page_to_image(pdf_path, page_num, dpi=72)
        low_dpi_tmp_path = os.path.join(pages_dir, f"page_{page_num+1}_low_detect.png")
        page_image_low.save(low_dpi_tmp_path)
        print(f"  [TIME] Page {page_num+1} Low-DPI Render (72): {time.time() - t0:.2f}s")

        # Layout detection (PaddleOCR PP-DocLayout) on small image
        t0 = time.time()
        raw_regions = detect_layout(low_dpi_tmp_path)
        print(f"  [TIME] Page {page_num+1} Layout Detection: {time.time() - t0:.2f}s")
        
        # Clean up temp low-dpi file
        if os.path.exists(low_dpi_tmp_path):
            os.remove(low_dpi_tmp_path)

        # 2. Rendering (High-DPI) for Element Extraction & UI
        t0 = time.time()
        page_image = pdf_page_to_image(pdf_path, page_num, dpi=300) # 300 DPI for AI clarity
        page_img_path = os.path.join(pages_dir, f"page_{page_num+1}.png")
        page_image.save(page_img_path)
        img_size = page_image.size
        img_height = img_size[1]
        img_w, img_h = img_size
        print(f"  [TIME] Page {page_num+1} High-DPI Render (300): {time.time() - t0:.2f}s")
        
        # 3. Refine and Scale coordinates
        # Refine on NORMALIZED [0-1.0] scale
        regions = refine_regions(raw_regions)
        
        # Scale back to RAW PIXELS of the 300 DPI image
        for r in regions:
            r["bbox"] = [
                r["bbox"][0] * img_w,
                r["bbox"][1] * img_h,
                r["bbox"][2] * img_w,
                r["bbox"][3] * img_h
            ]

        print(f"  [TIME] Page {page_num+1} Layout Detection: {time.time() - t0:.2f}s")
        
        with fitz.open(pdf_path) as pdf:
            pdf_page_size = (pdf[page_num].rect.width, pdf[page_num].rect.height)

        page_data = {
            "page_number": page_num + 1,
            "page_image": page_img_path,
            "elements": []
        }

        def process_element(elem_data):
            label, score, bbox = elem_data
            etype = classify_region(label)
            
            element = {
                "type": etype,
                "label": label,
                "confidence": score,
                "bbox_pixels": bbox,
            }
            
            tokens_local = 0
            requests_local = 0
            
            if etype == "text":
                pdf_bbox = scale_bbox_to_pdf(bbox, img_size, pdf_page_size)
                text = extract_text_from_bbox(pdf_path, page_num, pdf_bbox)
                element["content"] = text
                
            elif etype == "table":
                requests_local = 1 
                table_crop = crop_region(page_image, bbox, padding=20)
                fname = f"p{page_num+1}_t_{bbox[0]}_{bbox[1]}.png"
                t_path = os.path.join(tables_dir, fname)
                table_crop.save(t_path)
                element["image_path"] = t_path
                
                pdf_bbox = scale_bbox_to_pdf(bbox, img_size, pdf_page_size)
                res = extract_table_data(pdf_path, page_num + 1, pdf_bbox, t_path)
                element["structured_data"] = res
                tokens_local = res.get("token_usage", 0)
                
            return element, tokens_local, requests_local

        # Elements within page parallelization
        t0 = time.time()
        text_table_regions = []
        image_regions = []
        
        for r in regions:
            if classify_region(r["label"]) == "image":
                image_regions.append(r)
            else:
                text_table_regions.append(r)

        # 1. Process Text and Tables in Parallel (as before)
        other_tasks = [(r["label"], r["score"], r["bbox"]) for r in text_table_regions]
        with ThreadPoolExecutor(max_workers=10) as element_executor:
            other_results = list(element_executor.map(process_element, other_tasks))
            
        for result, tokens, requests in other_results:
            with total_tokens_lock:
                nonlocal_total_tokens += tokens
                nonlocal_total_requests += requests
            if result:
                page_data["elements"].append(result)

        # 2. Process Images in Batch
        if image_regions:
            images_to_batch = []
            for idx, r in enumerate(image_regions):
                label, score, bbox = r["label"], r["score"], r["bbox"]
                img_crop = crop_region(page_image, bbox, padding=5)
                fname = f"p{page_num+1}_i_{bbox[0]}_{bbox[1]}.png"
                i_path = os.path.join(images_dir, fname)
                img_crop.save(i_path)
                
                elem_id = f"PAGE_{page_num+1}_IMG_{idx+1}"
                
                element = {
                    "type": "image",
                    "label": label,
                    "confidence": score,
                    "bbox_pixels": bbox,
                    "image_path": i_path
                }
                
                images_to_batch.append({
                    "id": elem_id,
                    "image": img_crop,
                    "filename": fname,
                    "element": element
                })

            # Call the batch service
            batch_results, batch_tokens = batch_describe_images(images_to_batch, page_num + 1)
            
            with total_tokens_lock:
                nonlocal_total_tokens += batch_tokens
                nonlocal_total_requests += 1 # We count the batch as 1 request for stats

            # Map results back and apply safety net
            for item in images_to_batch:
                elem_id = item["id"]
                elem = item["element"]
                description = batch_results.get(elem_id)
                
                if not description or "[SKIP]" in description.upper() or "[ANALYSIS FAILED]" in description.upper():
                    # SAFETY NET: If the visual is large enough and has high layout confidence, 
                    # we retain it even if the vision summary is [SKIP]ped.
                    if elem["confidence"] > 0.4:
                        print(f"  [SAFETY NET] Retaining high-confidence technical figure: {elem['label']}")
                        elem["vision_description"] = f"Technical {elem['label'].capitalize()} (Summary Skipped/Inconclusive)"
                        page_data["elements"].append(elem)
                else:
                    elem["vision_description"] = description
                    page_data["elements"].append(elem)

        # Filtering and Sorting
        page_data["elements"] = [
            e for e in page_data["elements"]
            if not is_header_or_footer(e["bbox_pixels"], img_height)
        ]
        page_data["elements"] = sort_elements_by_reading_order(page_data["elements"])

        # Annotations
        annotated_path = os.path.join(annotated_dir, f"page_{page_num+1}_annotated.png")
        save_annotated_page(page_image.copy(), page_data["elements"], annotated_path, page_num)
        page_data["annotated_image"] = annotated_path

        print(f"--- [END] Page {page_num + 1} processed in {time.time() - start_page:.2f}s ---")
        return page_data

    # Main Parallel Execution of Pages
    # Setting max_workers to 5 suggests we process 5 pages at once.
    # Total RPM will be capped by our MultiClient logic anyway.
    with ThreadPoolExecutor(max_workers=5) as page_executor:
        results = list(page_executor.map(process_single_page, range(total_pages)))
    
    full_output["pages"] = results
    full_output["token_usage"] = nonlocal_total_tokens
    full_output["request_count"] = nonlocal_total_requests
    full_output["work_dir"] = work_dir
    
    total_tokens = nonlocal_total_tokens
    total_requests = nonlocal_total_requests

    total_time = time.time() - start_total
    print(f"\n✅ Extraction Complete! Total Time: {total_time:.2f}s, Total Tokens: {total_tokens}, AI Requests: {total_requests}")
    print(f"Artifacts saved to: {work_dir}")
    
    # Return the full data dictionary for DB storage
    return full_output, total_time, total_tokens, total_requests
