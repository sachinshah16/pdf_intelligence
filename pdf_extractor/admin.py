from django.contrib import admin
from .models import ExtractionTask

# @admin.register(ExtractionTask)
# class ExtractionTaskAdmin(admin.ModelAdmin):
#     list_display = ('id', 'pdf_file', 'status', 'created_at', 'updated_at')
#     list_filter = ('status', 'created_at', 'updated_at')
#     search_fields = ('pdf_file__filename',)
#     readonly_fields = ('created_at', 'updated_at')

admin.site.register(ExtractionTask)