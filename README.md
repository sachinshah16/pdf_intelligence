# PDF_AI

A Django-based PDF extraction and AI chat application that analyzes uploaded PDFs, extracts structured content, and provides AI-powered document conversation and export features.

## Key Features

- Upload PDFs and process them through a document extraction pipeline
- Extract text, page images, layout elements, tables, and visual annotations
- Store extraction results in the database for browsing and export
- Download generated Word reports for completed PDF tasks
- Send chat queries against extracted PDF context via AI
- Track usage metrics such as processing time, token usage, and request counts

## Technology Stack

- Django 5.x
- SQLite database
- PyMuPDF
- pdfplumber
- Pillow
- PaddleOCR / PaddlePaddle
- Google Gemini AI via `google-genai`
- python-docx

## Repository Structure

- `manage.py` - Django management entrypoint
- `InESS/` - Django project settings and URL routing
- `pdf_extractor/` - main application logic
  - `forms.py` - PDF upload form
  - `models.py` - `ExtractionTask` model and cleanup logic
  - `views.py` - upload, task detail, export, delete, and chat endpoints
  - `services/` - extraction, OCR, table parsing, AI chat, export, and utilities
  - `templates/` - frontend templates for upload, task list, detail views, and reports
- `media/` - uploaded PDFs and generated extraction output assets
- `requirements.txt` - Python package dependencies

## Prerequisites

- Python 3.12
- System libraries required by `paddleocr`, `PyMuPDF`, and image-processing dependencies
- macOS-specific setup may be needed for Paddle and native binaries

## Installation

1. Clone or open the repository.
2. Create and activate a virtual environment:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Apply database migrations:

```bash
python manage.py migrate
```

5. (Optional) Create a Django superuser:

```bash
python manage.py createsuperuser
```

## Running the Application

Start the Django development server:

```bash
python manage.py runserver
```

Then open:

- `http://127.0.0.1:8000/` to access the PDF extraction interface
- `http://127.0.0.1:8000/admin/` for Django admin (if enabled)

## Usage

1. Upload a PDF from the homepage.
2. The app processes the file and stores extraction results.
3. View the extracted document details and page gallery.
4. Download a generated Word report for a completed task.
5. Use the `/tasks/<task_id>/chat/` API endpoint to query AI with document context.

## Notes

- Uploaded PDFs are stored under `media/pdf_extractor/uploads/`.
- Extraction outputs are stored under `media/pdf_extractor/output/`.
- `MEDIA_ROOT` is configured in `InESS/settings.py`.
- `pdf_extractor/services/google_ai.py` currently contains the Gemini API key configuration.

## Recommended Improvements

- Move API key configuration to environment variables or a secrets store
- Add production-ready settings and static file handling
- Add tests for extraction and API flows

## Troubleshooting

- If PaddleOCR or `paddlepaddle` installation fails, verify your macOS Python version and install the correct Paddle wheel for your platform.
- Ensure the `media/` directory exists and is writeable by the Django process.
- If extraction fails on a PDF, review the `error.html` response for the stored error message.

## License

This project does not include a license file. Add one if you plan to share or reuse the code publicly.
