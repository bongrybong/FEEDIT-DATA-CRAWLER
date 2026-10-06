from django.apps import AppConfig


class AdminDashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
    verbose_name = "FEEDIT 데이터 대시보드"