from django.urls import path
from . import views

app_name = 'pdf_extractor'

urlpatterns = [
    path('', views.task_list, name='task_list'),
    path('upload/', views.upload_pdf, name='upload_pdf'),
    path('tasks/<int:task_id>/', views.task_detail, name='task_detail'),
    path('tasks/<int:task_id>/download/', views.download_word, name='download_word'),
    path('tasks/<int:task_id>/delete/', views.delete_task, name='delete_task'),
    path('tasks/<int:task_id>/chat/', views.chat_api, name='chat_api'),
    path('usage-report/', views.usage_report, name='usage_report'),
]
