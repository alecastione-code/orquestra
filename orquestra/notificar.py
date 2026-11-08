"""Notificações: Mac (osascript) e webhook opcional (config/notificacao.json). Nada de serviço inventado."""
import json
import os
import subprocess
import urllib.request

from . import config


def _config():
    for base in (os.path.join(config.casa_orquestra(), "config"), os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")):
        p = os.path.join(base, "notificacao.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
    return {}


def enviar(titulo, texto):
    """Devolve a lista de canais usados. Nunca levanta exceção."""
    usados, cfg = [], _config()
    if os.environ.get("ORQUESTRA_SEM_NOTIFICAR"):
        return usados
    if cfg.get("mac", True):
        try:
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')[:200]
            subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s"' % (esc(texto), esc(titulo))],
                           timeout=5, capture_output=True)
            usados.append("mac")
        except Exception:
            pass
    url = cfg.get("webhook_url")
    if url and url.startswith(("https://", "http://")):
        try:
            req = urllib.request.Request(url, data=texto.encode("utf-8"), headers={"Title": titulo.encode("ascii", "ignore").decode() or "Orquestra"})
            urllib.request.urlopen(req, timeout=5).read()
            usados.append("webhook")
        except Exception:
            pass
    return usados
