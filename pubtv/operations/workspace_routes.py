"""Compatibility entrances into the canonical operator workspaces."""
from urllib.parse import urlencode

from django.shortcuts import redirect, render


def entrance(destination, legacy_post=None, **defaults):
    """Retain local context while preserving old, validated POST contracts."""
    def view(request, *args, **kwargs):
        if request.method != 'GET' and legacy_post is not None:
            return legacy_post(request, *args, **kwargs)
        query = {**defaults, **request.GET.dict()}
        return redirect(destination + ('?' + urlencode(query) if query else ''))
    return view


def administration(request):
    return render(request, 'administration.html')
