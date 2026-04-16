import os
import time
import threading
import json
import re
from collections import deque
from google import genai
from google.genai import types

# API Key provided by the user
GEMINI_API_KEY = "AIzaSyC2XkS5_WBUegG4uZynRp_r20SakmnwzHE"

class RateLimiter:
    def __init__(self, rpm, tpm=None):
        self.rpm = rpm
        self.tpm = tpm
        self.requests = deque()
        self.tokens = deque()  # (timestamp, count)
        self.lock = threading.Lock()

    def _clean_old_records(self):
        now = time.time()
        while self.requests and now - self.requests[0] > 60:
            self.requests.popleft()
        while self.tokens and now - self.tokens[0][0] > 60:
            self.tokens.popleft()

    def can_make_request(self, estimated_tokens=0):
        with self.lock:
            self._clean_old_records()
            if len(self.requests) >= self.rpm:
                return False
            if self.tpm:
                current_tpm = sum(t[1] for t in self.tokens)
                if current_tpm + estimated_tokens > self.tpm:
                    return False
            return True

    def get_wait_time(self, estimated_tokens=0):
        """Calculate seconds until next request is allowed."""
        with self.lock:
            self._clean_old_records()
            wait_times = []
            if len(self.requests) >= self.rpm:
                wait_times.append(max(0, 60.5 - (time.time() - self.requests[0])))
            if self.tpm:
                current_tpm = sum(t[1] for t in self.tokens)
                if current_tpm + estimated_tokens > self.tpm:
                    wait_times.append(max(0, 60.5 - (time.time() - self.tokens[0][0])))
            return max(wait_times) if wait_times else 0

    def record_request(self, token_count):
        with self.lock:
            now = time.time()
            self.requests.append(now)
            if self.tpm:
                self.tokens.append((now, token_count))

class GemmaMultiClient:
    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.models = {
            "gemini-2.5-flash": RateLimiter(rpm=5, tpm=100000),
            "gemini-3.1-flash-lite-preview": RateLimiter(rpm=15, tpm=100000),
            "gemma-4-26b-a4b-it": RateLimiter(rpm=15, tpm=None),
            "gemma-3-12b-it": RateLimiter(rpm=30, tpm=15000),
            "gemma-3-27b-it": RateLimiter(rpm=30, tpm=15000)
        }
        self.priority_order = ["gemini-2.5-flash", "gemini-3.1-flash-lite-preview", "gemma-4-26b-a4b-it", "gemma-3-12b-it"]
        self.global_lock = threading.Lock() 

    def generate_content(self, prompt, image_bytes=None, mime_type="image/png", system_instruction=None, priority=None):
        """Generic method with automatic model selection and rate limiting."""
        # If prompt is a list, it's already a multi-part content list (for batching)
        if isinstance(prompt, list):
            contents = prompt
        else:
            contents = [prompt]
            
        if system_instruction:
            contents.insert(0, system_instruction)
            
        if image_bytes:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        
        # Define search order based on priority parameter
        if priority and priority in self.models:
            # When testing a specific model, we really want that one.
            search_order = [priority] + [m for m in self.priority_order if m != priority]
            # print(f"  [GEMMA] Forced Priority Order: {search_order}")
        else:
            search_order = self.priority_order

        while True:
            selected_model = None
            
            # 1. Pick an available model based on search order
            with self.global_lock:
                for model_id in search_order:
                    limiter = self.models[model_id]
                    if limiter.can_make_request(estimated_tokens=2000):
                        selected_model = model_id
                        break
            
            # 2. If no model available, find the min wait time
            if not selected_model:
                wait_times = [self.models[m].get_wait_time(2000) for m in search_order]
                sleep_time = min(wait_times)
                sleep_time = max(1.0, sleep_time)
                print(f"  [GEMMA] All models at rate limit. Waiting {sleep_time:.1f}s for RPM reset...")
                time.sleep(sleep_time)
                continue
            
            try:
                # If image_bytes is None, we assume the content is already in 'contents' (for batching)
                final_contents = contents.copy() if isinstance(contents, list) else [contents]
                if image_bytes:
                    final_contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

                response = self.client.models.generate_content(
                    model=selected_model,
                    contents=final_contents
                )
                
                tokens = 0
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    tokens = response.usage_metadata.total_token_count
                
                self.models[selected_model].record_request(tokens)
                return response.text, tokens, selected_model
            
            except Exception as e:
                error_str = str(e).lower()
                if "429" in error_str or "quota" in error_str:
                    print(f"  [GEMMA] 429 Rate Limit Hit on {selected_model}. Retrying...")
                    self.models[selected_model].record_request(1000)
                    time.sleep(1)
                    continue
                elif "503" in error_str or "unavailable" in error_str or "500" in error_str:
                    # Remove the failing model from our search order for THIS request only
                    search_order = [m for m in search_order if m != selected_model]
                    if not search_order:
                        print(f"  [GEMMA] CRITICAL: All models in priority list failed.")
                        return None, 0, "none"
                    
                    next_model = search_order[0]
                    print(f"  [GEMMA] Model {selected_model} is UNAVAILABLE (503/500). Falling back to {next_model}...")
                    continue # Re-loop and pick the next best model
                else:
                    print(f"  [GEMMA ERROR] {selected_model}: {e}")
                    return None, 0, selected_model

# GLOBAL CLIENT INSTANCE
_global_gemma_client = None
_client_lock = threading.Lock()

def get_gemma_client():
    global _global_gemma_client
    with _client_lock:
        if _global_gemma_client is None:
            _global_gemma_client = GemmaMultiClient()
    return _global_gemma_client

def batch_analyze_images_gemma(image_list: list):
    """
    Analyzes multiple images in a single request using index-based mapping.
    Expects a list of bytes: [img1_bytes, img2_bytes, ...]
    Returns: (dict_of_results, total_tokens) where keys in results are '1', '2', etc.
    """
    count = len(image_list)
    system_instruction = (
        f"You will receive {count} technical image(s) from a datasheet. "
        "Analyze each with absolute technical precision. "
        "Describe EVERYTHING: 1) Labels, part numbers, pin/signal names. 2) Every value with its unit. "
        "3) Diagram structure (connections, flow, relationships). 4) Embedded tables. "
        "5) Ratings, tolerances, or conditions. "
        "RELEVANCE RULE: If an image is a generic logo, return ONLY '[SKIP]' for its index. "
        "OUTPUT RULE: Your response MUST be a VALID JSON object mapping indices to descriptions. "
        f"The keys MUST be the strings '1' through '{count}' corresponding to the order of the images provided."
    )
    
    prompt = f"Analyze these {count} images in sequence. Return the mapping as JSON."
    
    # Construct strictly sequenced contents: [prompt, Image_1, Image_2, ...]
    contents = [prompt]
    for img_bytes in image_list:
        contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        
    client = get_gemma_client()
    response_text, tokens, model_name = client.generate_content(
        contents, # Use the list with images, not just the prompt string
        image_bytes=None, 
        system_instruction=system_instruction, 
        priority="gemini-3.1-flash-lite-preview"
    )
    
    if not response_text:
        return {}, 0, model_name
        
    try:
        # Search for the JSON block even if the model adds conversational text
        match = re.search(r'(\{.*\})', response_text, re.DOTALL)
        if match:
            clean_json = match.group(1)
        else:
            # Fallback to stripping markdown blocks
            clean_json = re.sub(r'```json\s*|\s*```', '', response_text).strip()
            
        # SANITIZE: AI often fails to escape backslashes in technical descriptions
        # Fix: Escape single backslashes that are not followed by valid escape chars
        clean_json = re.sub(r'\\(?![\\\"\/bfnrtu])', r'\\\\', clean_json)
            
        results = json.loads(clean_json)
        return results, tokens, model_name
    except Exception as e:
        # If the response is truly non-JSON, the caller (vision.py) will trigger fallback
        print(f"  [GEMMA] Batch JSON Parse Error: {e}")
        return {}, tokens, model_name

def extract_table_gemma(image_bytes):
    """Zero-Talk JSON table extraction (English-only)."""
    system_instruction = (
        "You are a raw data extractor. Your output MUST ONLY be a valid JSON object. "
        "DO NOT use markdown code blocks (```json). DO NOT provide any explanation. "
        "Strictly repeat values for merged cells to maintain a valid grid."
    )
    prompt = (
        "Extract the table from the image as a JSON object: "
        "{\"headers\": [\"Col1\", \"Col2\"], \"rows\": [[\"R1V1\", \"R1V2\"], [...]]} "
        "RULES: 1. No intro/outro text. 2. No markdown blocks. 3. Numeric values must be strings. 4. Precise grid only."
    )
    client = get_gemma_client()
    return client.generate_content(prompt, image_bytes, system_instruction=system_instruction, priority="gemini-2.5-flash")

def analyze_image_gemma(image_bytes):
    """Ultra-concise technical summary (English-only)."""
    system_instruction = (
        "You are a technical annotator. Provide a super-condensed 2-3 sentence technical summary "
        "of the visual. Focus only on key variables, nomenclature, and trends. NO intros like 'This image shows...'."
    )
    prompt = (
        "Analyze this engineering visual. "
        "PRECISION RULE: Engineering Nomenclature, Legends, Part Numbering Systems, and Legend Charts are HIGH-VALUE technical artifacts. "
        "RELEVANCE RULE: If it is a generic logo, decorative icon, or abstract filler, return ONLY the word [SKIP]. "
        "Otherwise, return a strictly 2-3 sentence technical summary of the diagram."
    )
    client = get_gemma_client()
    return client.generate_content(prompt, image_bytes, system_instruction=system_instruction, priority="gemini-3.1-flash-lite-preview")

def chat_with_pdf_gemma(messages, context_string, priority=None):
    """
    Stateless chat with PDF context, optimized for speed using Gemini 3.1 Flash Lite.
    Supports a 'priority' override for model testing.
    """
    system_instruction = (
        "You are an expert PDF Intelligence Assistant. "
        "Answer questions ONLY based on the provided DOCUMENT CONTEXT below. "
        "If the answer is not in the context, say 'I cannot find that information in the document.' "
        "Use Markdown for formatting. Be technical and precise. If asked about images, graphs,or tables, refer to the document context and try to explain that data in general use case so that it become easy to understand for a user who is not aware of the document.\n\n"
        f"--- DOCUMENT CONTEXT START ---\n{context_string}\n--- DOCUMENT CONTEXT END ---"
    )
    
    client = get_gemma_client()
    
    conversation_summary = "Conversation History:\n"
    for msg in messages[:-1]:
        conversation_summary += f"{msg['role'].capitalize()}: {msg['content']}\n"
    
    final_prompt = f"{conversation_summary}\nUser: {messages[-1]['content']}"
    
    # Priority defaults to Flash Lite if not specified
    target_priority = priority or "gemini-3.1-flash-lite-preview"
    
    response_text, tokens, model_name = client.generate_content(
        final_prompt, 
        system_instruction=system_instruction, 
        priority=target_priority
    )
    return response_text, tokens, model_name
