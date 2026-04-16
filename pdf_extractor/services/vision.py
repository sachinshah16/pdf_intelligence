import os
from PIL import Image
import io
from .google_ai import analyze_image_gemma, batch_analyze_images_gemma
import time

def pil_image_to_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

def describe_image_moondream(image: Image.Image, page_num: int, filename: str = "image.png", prompt: str = None):
    """
    Renamed for backward compatibility in pipeline.py, but now using Gemma API.
    """
    model_name = "gemini-3.1-flash-lite-preview"
    print(f"  [PAGE {page_num} | IMAGE] Starting visual analysis: {filename}... (Model: {model_name})")
    try:
        image_bytes = pil_image_to_bytes(image)
        # We ignore the passed prompt and use our refined technical analysis prompt
        result, tokens = analyze_image_gemma(image_bytes)
        
        if not result or "[SKIP]" in result.upper():
            print(f"  [PAGE {page_num} | IMAGE] Analysis complete: [SKIPPED/NON-TECHNICAL]")
            return None, tokens
            
        summary = result.strip()
        preview = (summary[:75] + '...') if len(summary) > 75 else summary
        print(f"  [PAGE {page_num} | IMAGE] Analysis complete. Result: {preview}")
        return summary, tokens
    except Exception as e:
        print(f"  [PAGE {page_num} | IMAGE] ERROR: {e}")
        return f"Error analyzing image: {str(e)}", 0

def batch_describe_images(image_data_list: list, page_num: int):
    """
    Processes a list of image elements in batches of up to 7.
    image_data_list: list of dicts: {"image": PIL, "filename": str, "id": str}
    Returns: (dict_of_results, total_tokens)
    """
    if not image_data_list:
        return {}, 0

    MAX_BATCH_SIZE = 7
    all_results = {}
    total_tokens = 0
    
    # Split into chunks of 7
    chunks = [image_data_list[i : i + MAX_BATCH_SIZE] for i in range(0, len(image_data_list), MAX_BATCH_SIZE)]
    
    for chunk_idx, chunk in enumerate(chunks):
        batch_id = f"BATCH {chunk_idx + 1}/{len(chunks)}"
        
        # Collect short filenames for logging
        short_names = [os.path.basename(item["filename"]) for item in chunk]
        print(f"  [PAGE {page_num} | IMAGE] Starting {batch_id}: {', '.join(short_names)}")
        
        # Prepare list of bytes for AI (order is critical for mapping)
        image_bytes_list = []
        for item in chunk:
            image_bytes_list.append(pil_image_to_bytes(item["image"]))
            
        try:
            # results will have keys '1', '2', '3' etc.
            results, tokens, used_model = batch_analyze_images_gemma(image_bytes_list)
            total_tokens += tokens
            
            if not results:
                print(f"  [PAGE {page_num} | IMAGE] {batch_id} returned empty. Falling back to one-by-one...")
                results, retry_tokens = retry_batch_one_by_one(chunk, page_num)
                total_tokens += retry_tokens
                all_results.update(results)
            else:
                # Map numeric results back to original IDs in the chunk
                for i, item in enumerate(chunk):
                    idx_str = str(i + 1)
                    desc = results.get(idx_str, "[Analysis Failed]")
                    all_results[item["id"]] = desc

            print(f"  [PAGE {page_num} | IMAGE] {batch_id} completed. (Tokens: {total_tokens}) | Model: {used_model}")
            
        except Exception as e:
            print(f"  [PAGE {page_num} | IMAGE] {batch_id} FAILED: {e}. Falling back to one-by-one...")
            results, retry_tokens = retry_batch_one_by_one(chunk, page_num)
            total_tokens += retry_tokens
            all_results.update(results)
            
    return all_results, total_tokens

def retry_batch_one_by_one(chunk, page_num):
    """Fallback if batch processing fails."""
    results = {}
    total_tokens = 0
    for item in chunk:
        fname = os.path.basename(item["filename"])
        print(f"  [PAGE {page_num} | IMAGE] Starting visual analysis: {fname}...")
        
        try:
            img_bytes = pil_image_to_bytes(item["image"])
            desc, tokens, used_model = analyze_image_gemma(img_bytes)
            
            if not desc or "[SKIP]" in desc.upper():
                print(f"  [PAGE {page_num} | IMAGE] Analysis complete: [SKIPPED/NON-TECHNICAL] | Model: {used_model}")
                results[item["id"]] = "[SKIP]"
            else:
                print(f"  [PAGE {page_num} | IMAGE] Analysis complete. Result: {desc[:80]}... | Model: {used_model}")
                results[item["id"]] = desc
                
            total_tokens += tokens
        except Exception as e:
            print(f"  [PAGE {page_num} | IMAGE] FAILED for {fname}: {e}")
            results[item["id"]] = "[Analysis Failed]"
            
    return results, total_tokens
