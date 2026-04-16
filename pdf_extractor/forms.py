from django import forms
from .models import ExtractionTask

class PDFUploadForm(forms.ModelForm):
    class Meta:
        model = ExtractionTask
        fields = ['pdf_file']
