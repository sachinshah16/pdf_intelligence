from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse, JsonResponse
from .models import ExtractionTask
from .forms import PDFUploadForm
from .services.pipeline import process_pdf_pipeline
from .services.chat import build_document_context, build_page_context
from .services.google_ai import chat_with_pdf_gemma
from .services.export import generate_word_report_stream
import os
import json
from pathlib import Path

def get_media_url(full_path):
    if not full_path:
        return ""
    from django.conf import settings
    # Normalize slashes first
    norm_path = full_path.replace('\\', '/')
    
    # Handle various path formats to extract the part after 'media/'
    if 'media/' in norm_path:
        # If it contains 'media/', split and take the last part
        rel_path = norm_path.split('media/')[-1]
    else:
        # Otherwise assume it's already relative to media root
        rel_path = norm_path
        
    # Ensure no leading slash on rel_path to avoid double slashes with MEDIA_URL
    rel_path = rel_path.lstrip('/')
    
    # Clean construction of final URL
    base_url = settings.MEDIA_URL
    if not base_url.endswith('/'):
        base_url += '/'
        
    return base_url + rel_path

def upload_pdf(request):
    """View to upload a PDF and trigger extraction."""
    if request.method == "POST":
        form = PDFUploadForm(request.POST, request.FILES)
        if form.is_valid():
            task = form.save()
            task.status = 'processing'
            task.save()
            
            try:
                # Trigger the pipeline
                output_dir = os.path.join('media', 'pdf_extractor', 'output')
                full_data, exec_time, total_tok, total_req = process_pdf_pipeline(task.pdf_file.path, output_dir=output_dir)
                
                # NEW Architecture: Save data to DB
                task.extracted_data = full_data
                task.processing_time = exec_time
                task.token_usage = total_tok
                task.request_count = total_req
                task.status = 'completed'
                task.save()
                return redirect(reverse('pdf_extractor:task_detail', args=[task.id]))
            except Exception as e:
                task.status = 'failed'
                task.error_message = str(e)
                task.save()
                return render(request, 'error.html', {'task': task})
    else:
        form = PDFUploadForm()
    
    return render(request, 'extraction_form.html', {'form': form})

def task_detail(request, task_id):
    """View results from DB-stored extraction data."""
    task = get_object_or_404(ExtractionTask, id=task_id)
    
    if task.status != 'completed' or not task.extracted_data:
        return render(request, 'detail.html', {'task': task})

    # Get requested page
    try:
        page_num = int(request.GET.get('page', 1))
    except ValueError:
        page_num = 1
            
    full_data = task.extracted_data
    total_pages = len(full_data.get('pages', []))
    if page_num < 1: page_num = 1
    if page_num > total_pages: page_num = total_pages
    
    page_index = page_num - 1
    page_data = full_data['pages'][page_index].copy()
    
    # Convert paths to URLs (Paths are relative to media root)
    page_data['image_url'] = get_media_url(page_data.get('page_image'))
    for elem in page_data.get('elements', []):
        if 'image_path' in elem:
            elem['image_url'] = get_media_url(elem['image_path'])
            
    # Gallery for Global Mode
    all_page_images = []
    for p in full_data.get('pages', []):
        all_page_images.append({
            'page_number': p.get('page_number'),
            'image_url': get_media_url(p.get('page_image'))
        })
            
    return render(request, 'detail.html', {
        'task': task,
        'page_data': page_data,
        'current_page': page_num,
        'total_pages': total_pages,
        'page_range': range(1, total_pages + 1),
        'all_page_images': all_page_images
    })

from django.db.models import Sum, Avg, Count

def usage_report(request):
    """Management view for AI performance and token usage."""
    tasks = ExtractionTask.objects.all().order_by('-created_at')
    
    stats = tasks.aggregate(
        total_tasks=Count('id'),
        total_tokens=Sum('token_usage'),
        total_requests=Sum('request_count'),
        total_time=Sum('processing_time'),
        avg_time=Avg('processing_time'),
        avg_tokens=Avg('token_usage'),
        avg_requests=Avg('request_count')
    )
    
    # Task breakdown by status
    status_counts = tasks.values('status').annotate(count=Count('status'))
    
    context = {
        'tasks': tasks[:50], # Recent 50 tasks
        'stats': stats,
        'status_counts': status_counts
    }
    return render(request, 'usage_report.html', context)

def task_list(request):
    """List all extraction tasks."""
    tasks = ExtractionTask.objects.all().order_by('-created_at')
    return render(request, 'list.html', {'tasks': tasks})

def download_word(request, task_id):
    """Generate and download Word report on-demand from DB data."""
    task = get_object_or_404(ExtractionTask, id=task_id)
    if task.status == 'completed' and task.extracted_data:
        # Inject database metrics into the data dict for the report summary
        report_data = task.extracted_data.copy()
        report_data["processing_time"] = task.processing_time
        report_data["token_usage"] = task.token_usage
        report_data["request_count"] = task.request_count
        
        doc_stream = generate_word_report_stream(report_data)
        
        filename = f"{Path(task.pdf_file.name).stem}_report.docx"
        response = HttpResponse(
            doc_stream.getvalue(), 
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    return HttpResponse("Data not available for export.", status=404)

def delete_task(request, task_id):
    """Deletes a task and its associated PDF file and directory."""
    task = get_object_or_404(ExtractionTask, id=task_id)
    
    # Process cleanup of associated files if necessary (like the PDF itself)
    if task.pdf_file and os.path.exists(task.pdf_file.path):
        os.remove(task.pdf_file.path)
        
    task.delete()
    return redirect('pdf_extractor:task_list')

def chat_api(request, task_id):
    """
    API endpoint for stateless AI chat. 
    Expects POST with JSON: {"messages": [...]}
    Returns JSON: {"response": "...", "tokens": 123}
    """
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    task = get_object_or_404(ExtractionTask, id=task_id)
    
    # 1. Get Context Data (Prioritize DB JSONField)
    data = task.extracted_data
    
    if not data:
        return JsonResponse({"error": "Document analysis results not found in database."}, status=404)

    try:
        body = json.loads(request.body)
        messages = body.get('messages', [])
        page_num = body.get('page_num') # Optional localized context
        priority = body.get('model')    # Model testing override
        
        print(f"[DEBUG] Chat API received model preference: {priority}")
        
        if not messages:
            return JsonResponse({"error": "No messages found"}, status=400)

        # 2. Build context (Page-specific OR Full-document)
        if page_num:
            context = build_page_context(data, int(page_num))
        else:
            context = build_document_context(data)
        
        # 2. Call AI logic with the requested model priority
        import time
        start_time = time.time()
        ai_response, tokens, model_id = chat_with_pdf_gemma(messages, context, priority=priority)
        duration = time.time() - start_time
        
        print(f"  [CHAT] Reply generated via {model_id} ({tokens} tokens) in {duration:.2f}s")
        
        if ai_response:
            return JsonResponse({
                "response": ai_response,
                "tokens": tokens,
                "time": round(duration, 2),
                "mode": "page" if page_num else "document"
            })
        else:
            return JsonResponse({"error": "AI failed to respond"}, status=500)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
