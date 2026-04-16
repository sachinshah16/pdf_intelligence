import pdfplumber
import re

def filter_english_only(text: str) -> str:
    """Keep only printable ASCII characters and common Latin-1 symbols (English-friendly)."""
    if not text:
        return ""
    # Regex keeps: alphanumeric, space, common punctuation, and Latin-1 symbols like µ, Ω
    # Matches: A-Z, a-z, 0-9, space, !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~
    # Plus common engineering symbols: µ (u00B5), Ω (u03A9), etc.
    pattern = re.compile(r'[^\x20-\x7E\s\u00B5\u03A9\u00B0\u00B1\u00B2\u00B3\u00BC\u00BD\u00BE]')
    cleaned = pattern.sub('', text)
    # Remove multiple spaces/newlines that might result from stripping
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def extract_text_from_bbox(pdf_path: str, page_num: int, pdf_bbox: tuple) -> str:
    """
    Extract text from a specific bounding box on a PDF page using pdfplumber's 
    word-level extraction for precise column-aware reconstruction.
    pdf_bbox: (x1, y1, x2, y2) in PDF coordinate space
    """
    with pdfplumber.open(pdf_path) as pdf:
        if page_num >= len(pdf.pages):
            return ""
        page = pdf.pages[page_num]
        
        # Crop the page to the region of interest
        cropped = page.crop(pdf_bbox)
        
        # 1. Extract individual words with their positions
        words = cropped.extract_words(x_tolerance=3, y_tolerance=3)
        
        if not words:
            return ""
            
        # 2. Sort words by (y, x) to ensure correct reading flow
        # We group words into 'lines' based on a small y_tolerance
        lines = []
        if words:
            # Sort words by top position (y)
            words.sort(key=lambda w: w['top'])
            
            # Group into lines
            current_line = [words[0]]
            for i in range(1, len(words)):
                prev_word = words[i-1]
                curr_word = words[i]
                
                # If the y-difference is small, they are on the same line
                if abs(curr_word['top'] - prev_word['top']) < 3:
                    current_line.append(curr_word)
                else:
                    # Sort the current line by x (left-to-right)
                    current_line.sort(key=lambda w: w['x0'])
                    lines.append(current_line)
                    current_line = [curr_word]
            
            # Don't forget the last line
            current_line.sort(key=lambda w: w['x0'])
            lines.append(current_line)
            
        # 3. Join words and lines
        final_text = "\n".join([" ".join([w['text'] for w in line]) for line in lines])
        
        return final_text.strip()
