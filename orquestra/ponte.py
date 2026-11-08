"""`orq ponte`: o lado do Mac da Orquestra no WhatsApp. A cada 5 min envia a foto ao painel online e aplica os comandos
pendentes (aprovar/recusar melhoria). O token fica no Keychain (serviço orquestra-ponte): nunca em arquivo nem em log.
Sem token = não faz nada e sai calado."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import aovivo, custos, db, melhorias
from .segredos import ocultar

# Endereço do painel: ORQUESTRA_PONTE_URL ou o arquivo `ponte_url.txt` na casa do Orquestra (fora do repositório).
ARQ_URL = "ponte_url.txt"
LIMITE_FOTO = 60_000     # o servidor aceita até 64 KB
TIMEOUT = 15


def token():
    """Token da ponte: ORQUESTRA_PONTE_TOKEN (só para testes/CI) ou Keychain. None se não houver."""
    t = os.environ.get("ORQUESTRA_PONTE_TOKEN")
    if t:
        return t.strip()
    if os.environ.get("ORQUESTRA_SEM_KEYCHAIN"):
        return None
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "orquestra-ponte", "-w"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def base_url():
    b = os.environ.get("ORQUESTRA_PONTE_URL") or ""
    if not b:
        try:
            from . import config
            with open(os.path.join(config.casa_orquestra(), ARQ_URL)) as f:
                b = f.read().strip()
        except OSError:
            b = ""
    if not b:
        raise ValueError("sem endereço da ponte: defina ORQUESTRA_PONTE_URL ou ponte_url.txt na casa do Orquestra")
    b = b.rstrip("/")
    u = urlparse(b)
    if u.scheme != "https" and u.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("a ponte só fala https (ou localhost nos testes)")
    return b


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z") if t else None


def foto(agora=None):
    """Foto no formato do contrato; todo texto ≤ 140 e sem segredo."""
    agora = agora or time.time()
    fila = []
    for i in aovivo.fotografia()["itens"]:
        if i["estado"] in ("terminou",):
            continue
        fila.append({"id": str(i["id"])[:40], "nome": ocultar(i.get("titulo") or i["nome"], 140), "executor": {"orquestrador": "Orquestrador", "claude": "Claude", "codex": "Codex", "agente": "Agente"}.get(i["tipo"], i["tipo"]),
                     "modelo": str(i.get("modelo") or "?")[:40], "estado": i["estado"], "etapa": ocultar(i.get("etapa_agente") or i.get("etapa") or "", 140)})
    rel = custos.relatorio("dia", "modelo")
    cs = db.cotas()
    ml = melhorias.lista("sugerida")
    corpo = {"geradoEm": _iso(agora), "fila": fila[:40],
             "custosDia": {"totalBRL": rel["total_brl"], "porModelo": [{"modelo": a["chave"], "brl": a["brl"]} for a in rel["agrupado"][:8]], "economiaBRL": rel["economia_vs_orquestrador"]},
             "cotas": [{"agente": c["agente"], "usadoPct": float(c["usado_pct"] or 0), "renovaEm": _iso(c["renova_em"]) or ""} for c in cs],
             "melhorias": [{"id": m["id"], "titulo": ocultar(m["problema"], 140), "risco": m["risco"] or "?", "estado": m["estado"]} for m in ml[:20]]}
    while len(json.dumps(corpo, ensure_ascii=False).encode()) > LIMITE_FOTO and corpo["fila"]:
        corpo["fila"].pop()
    return corpo


def _http(metodo, url, tk, corpo=None):
    dados = json.dumps(corpo, ensure_ascii=False).encode() if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo, headers={"Authorization": "Bearer " + tk, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        txt = r.read()
    return json.loads(txt) if txt else {}


def aplicar(c):
    """(ok, mensagem ≤ 200) de um comando; nunca levanta."""
    try:
        alvo = int(c.get("alvo"))
        if c.get("tipo") == "aprovar_melhoria":
            tid = melhorias.aprovar(alvo)
            return True, "melhoria %d aprovada → tarefa #%d" % (alvo, tid)
        if c.get("tipo") == "recusar_melhoria":
            melhorias.recusar(alvo, "pedido pelo WhatsApp")
            return True, "melhoria %d recusada" % alvo
        return False, "comando desconhecido: %s" % str(c.get("tipo"))[:60]
    except (KeyError, ValueError, TypeError) as e:
        return False, str(e.args[0] if e.args else e)[:200]


def sincronizar():
    """Devolve {'enviada': bool, 'comandos': n, 'erro': str|None}; sem token devolve None (e não faz nada)."""
    tk = token()
    if not tk:
        return None
    r = {"enviada": False, "comandos": 0, "erro": None}
    try:
        b = base_url()
        _http("PUT", b + "/api/orquestra/foto", tk, foto())
        r["enviada"] = True
        for c in (_http("GET", b + "/api/orquestra/comandos", tk).get("comandos") or []):
            ok, msg = aplicar(c)
            _http("POST", "%s/api/orquestra/comandos/%s/feito" % (b, urllib.request.quote(str(c.get("id")), safe="")), tk, {"ok": ok, "msg": msg})
            r["comandos"] += 1
    except urllib.error.HTTPError as e:
        r["erro"] = "HTTP %d" % e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        r["erro"] = ocultar(str(getattr(e, "reason", e)), 120)      # nunca inclui cabeçalho nem token
    return r
