"""Isolated URL configuration used only to exercise Django's 500 dispatch."""

from django.urls import path


def intentional_server_error(request):
    raise RuntimeError("intentional public error renderer test")


handler500 = "core.views.public_server_error"

urlpatterns = [
    path("intentional-server-error/", intentional_server_error),
]
