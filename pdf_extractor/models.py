from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
import os
import shutil
from pathlib import Path

class ExtractionTask(models.Model):
    # ... (existing fields)
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    pdf_file = models.FileField(upload_to='pdf_extractor/uploads/')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    extracted_data = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    processing_time = models.FloatField(default=0.0) 
    token_usage = models.IntegerField(default=0)     
    request_count = models.IntegerField(default=0)   

    @property
    def filename(self):
        return os.path.basename(self.pdf_file.name)

    @property
    def formatted_time(self):
        if self.processing_time < 0.1:
            return "0s"
        if self.processing_time >= 60:
            minutes = int(self.processing_time // 60)
            seconds = int(self.processing_time % 60)
            return f"{minutes}m {seconds}s"
        return f"{self.processing_time:.1f}s"

    def __str__(self):
        return f"Task {self.id}: {self.filename} ({self.status})"

@receiver(post_delete, sender=ExtractionTask)
def cleanup_task_files(sender, instance, **kwargs):
    """
    Triggered after a task is deleted. Removes associated media files with safety checks.
    """
    from django.conf import settings
    
    # 1. Delete the original PDF file
    if instance.pdf_file:
        try:
            if os.path.isfile(instance.pdf_file.path):
                os.remove(instance.pdf_file.path)
        except Exception as e:
            print(f"  [CLEANUP ERROR] Failed to delete PDF file: {e}")

    # 2. Delete the entire output workspace folder
    work_dir = None
    if instance.extracted_data:
        work_dir = instance.extracted_data.get('work_dir')
    
    # Fallback for old records without 'work_dir'
    if not work_dir and instance.pdf_file:
        pdf_name = Path(instance.pdf_file.path).stem
        work_dir = os.path.join(settings.MEDIA_ROOT, 'pdf_extractor', 'output', pdf_name)

    if work_dir:
        try:
            # SAFETY GUARD: Ensure work_dir is absolute and inside MEDIA_ROOT/pdf_extractor/output
            abs_work_dir = os.path.abspath(work_dir)
            media_root = os.path.abspath(settings.MEDIA_ROOT)
            allowed_base = os.path.join(media_root, 'pdf_extractor', 'output')
            
            if not abs_work_dir.startswith(allowed_base) or abs_work_dir == allowed_base:
                print(f"  [CLEANUP SAFETY] Blocked deletion of suspicious path: {abs_work_dir}")
                return

            if os.path.isdir(abs_work_dir):
                shutil.rmtree(abs_work_dir)
                print(f"  [CLEANUP] Deleted workspace for Task {instance.id}: {abs_work_dir}")
        except Exception as e:
            print(f"  [CLEANUP ERROR] Failed to delete {work_dir}: {e}")
