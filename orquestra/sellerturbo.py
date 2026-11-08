"""Integração Seller Turbo: fila de PRs, gates e últimos deploys via `gh` (só leitura) + fila-unica.out."""
import json
import os
import re
import shutil
import subprocess
import time

from . import config, db

PREFIXOS = re.compile(r"^(qa/|fix/|jarvis/|t\d+)", re.I)
_cache = {"quando": 0, "dados": {}}
_gates = {}   # numero -> último estado do gate (para alertar na virada)


def _gh():
    path = os.pathsep.join([os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH", "")])
    return shutil.which("gh", path=path), path


def _rodar(args, timeout=25):
    if os.environ.get("ORQUESTRA_SEM_GH"):
        raise RuntimeError("gh desligado (ORQUESTRA_SEM_GH)")
    exe, path = _gh()
    if not exe:
        raise RuntimeError("gh não encontrado")
    env = dict(os.environ, PATH=path, GH_PROMPT_DISABLED="1", NO_COLOR="1")
    r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "gh falhou").strip().splitlines()[-1][:200])
    return json.loads(r.stdout or "[]")


def gate(rollup):
    """→ ('falhou'|'pendente'|'ok'|'sem checks', [nomes dos checks que falharam])."""
    if not rollup:
        return "sem checks", []
    falhos = [c.get("name") or c.get("context") for c in rollup
              if (c.get("conclusion") or c.get("state") or "").upper() in ("FAILURE", "TIMED_OUT", "ERROR", "STARTUP_FAILURE")]
    if falhos:
        return "falhou", falhos
    if any((c.get("status") or c.get("state") or "").upper() in ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING", "REQUESTED") for c in rollup):
        return "pendente", []
    return "ok", []


SUITE = re.compile(r"^Release tests \d/\d$")


def gate_de_verdade(falhos, rotulos):
    """Fora da vez (sem rótulo fila:vez) o release-gate recusa com "exige a suíte completa": só conta se a suíte (Release tests N/2) falhou."""
    return "fila:vez" in rotulos or any(SUITE.match(f or "") for f in falhos)


def resumir_prs(prs):
    saida = []
    for p in prs:
        ramo = p.get("headRefName", "")
        if not PREFIXOS.match(ramo):
            continue
        g, falhos = gate(p.get("statusCheckRollup"))
        if g == "falhou" and not gate_de_verdade(falhos, {r.get("name") for r in p.get("labels") or []}):
            g = "fora da vez"
        saida.append({"numero": p["number"], "titulo": p.get("title", ""), "ramo": ramo, "rascunho": p.get("isDraft", False),
                      "gate": g, "falhos": falhos, "atualizado": p.get("updatedAt"), "url": p.get("url")})
    return sorted(saida, key=lambda x: x["numero"], reverse=True)


def resumir_deploys(runs):
    return [{"id": r.get("databaseId"), "titulo": r.get("displayTitle", ""), "workflow": r.get("workflowName", ""),
             "ramo": r.get("headBranch", ""), "status": r.get("status"), "resultado": r.get("conclusion") or "em andamento",
             "quando": r.get("createdAt"), "url": r.get("url")} for r in runs]


def fila_unica(n=8):
    p = os.path.join(config.pasta_logs_codex(), "fila-unica.out")
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 4096))
            return [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()][-n:]
    except OSError:
        return []


def alertas(prs, antes):
    """PRs cujo gate virou 'falhou' desde a leitura anterior."""
    novos = []
    for p in prs:
        if p["gate"] == "falhou" and antes.get(p["numero"]) not in (None, "falhou"):
            novos.append(p)
    return novos


def atualizar(repo=None):
    repo = repo or config.repo_seller_turbo()
    d = {"repo": repo, "quando": time.time(), "prs": [], "deploys": [], "fila_unica": fila_unica(), "erro": None}
    try:
        d["prs"] = resumir_prs(_rodar(["pr", "list", "--repo", repo, "--state", "open", "--limit", "60", "--json",
                                       "number,title,headRefName,statusCheckRollup,updatedAt,isDraft,url,labels"]))
        d["deploys"] = resumir_deploys(_rodar(["run", "list", "--repo", repo, "--limit", "8", "--json",
                                               "databaseId,displayTitle,status,conclusion,workflowName,createdAt,headBranch,url"]))
    except Exception as e:  # sem rede / gh deslogado: o painel mostra o erro, nada quebra
        d["erro"] = str(e)
    if not d["erro"]:
        for p in alertas(d["prs"], _gates):
            txt = "GATE FALHOU no PR #%d (%s): %s" % (p["numero"], p["ramo"], ", ".join(p["falhos"])[:120])
            with db.conectar() as con:
                db.evento(con, "alerta", txt)
            db.msg("orquestra", "orquestrador", txt)
            try:
                from . import notificar
                notificar.enviar("Orquestra · gate", txt)
            except Exception:
                pass
        _gates.clear()
        _gates.update({p["numero"]: p["gate"] for p in d["prs"]})
        d["gates_falhos"] = sum(1 for p in d["prs"] if p["gate"] == "falhou")
    _cache.update(quando=d["quando"], dados=d)
    return d


def retrato(max_idade=150):
    if time.time() - _cache["quando"] > max_idade:
        return atualizar()
    return _cache["dados"]


_ci = {"quando": 0, "dados": None}
_mesclados = {"quando": 0, "dados": None}


def ci_minutos(repo=None, max_idade=600):
    """Minutos de CI por dia (duração de cada run: updatedAt − startedAt). None se o gh não responder."""
    from datetime import datetime
    if time.time() - _ci["quando"] < max_idade and _ci["dados"] is not None:
        return _ci["dados"]
    try:
        runs = _rodar(["run", "list", "--repo", repo or config.repo_seller_turbo(), "--limit", "100", "--json",
                       "databaseId,startedAt,updatedAt,workflowName"], timeout=30)
    except Exception:
        return None
    por_dia = {}
    for r in runs:
        try:
            a = datetime.fromisoformat(r["startedAt"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(r["updatedAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue
        dia = a.astimezone().strftime("%Y-%m-%d")
        por_dia[dia] = por_dia.get(dia, 0.0) + max(0.0, (b - a).total_seconds() / 60.0)
    _ci.update(quando=time.time(), dados=por_dia)
    return por_dia


def prs_mesclados(repo=None, max_idade=600):
    """PRs mesclados por dia {dia: n}. None se o gh não responder."""
    from datetime import datetime
    if time.time() - _mesclados["quando"] < max_idade and _mesclados["dados"] is not None:
        return _mesclados["dados"]
    try:
        prs = _rodar(["pr", "list", "--repo", repo or config.repo_seller_turbo(), "--state", "merged", "--limit", "100", "--json", "number,mergedAt"], timeout=30)
    except Exception:
        return None
    por_dia = {}
    for p in prs:
        try:
            dia = datetime.fromisoformat(p["mergedAt"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
            por_dia[dia] = por_dia.get(dia, 0) + 1
        except (KeyError, ValueError, AttributeError):
            continue
    _mesclados.update(quando=time.time(), dados=por_dia)
    return por_dia
