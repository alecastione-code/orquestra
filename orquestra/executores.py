"""Cada executor vira tarefa no quadro sozinho: subagentes das sessões Claude (`<projeto>/<sessão>/subagents/agent-*.jsonl`)
e rollouts soltos do Codex. Título, modelo, sessão-mãe, início, fim, tokens, custo equivalente e resultado em 3 linhas.
Idempotente: a chave é `tarefas.ref` (`sub:<arquivo>` / `codex:<rollout>`). Nada de texto bruto: só título e resultado curtos, sem segredo."""
import glob
import json
import os
import time

from . import cerebro, coletores, config, custo, db, transcritos
from .segredos import ocultar

ATIVO_S = 900          # sem escrita há mais de 15 min = não está mais rodando
CAMPOS = ("titulo", "estado", "resultado", "iniciado", "concluido", "atualizado", "modelo", "nivel", "executor", "projeto",
          "tokens_in", "tokens_out", "tokens_cache", "custo_brl", "sessao_mae")


def _meta(caminho):
    try:
        with open(caminho[:-6] + ".meta.json", encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _sub_arquivos(dias):
    corte = time.time() - dias * 86400
    out = []
    for p in glob.glob(os.path.join(config.claude_home(), "projects", "**", "subagents", "agent-*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(p) >= corte and not coletores._proibido(p):
                out.append(p)
        except OSError:
            pass
    return out


def _primeira_linha(txt, n=100):
    for l in str(txt or "").splitlines():
        l = l.strip().lstrip("#").strip()
        if l:
            return ocultar(l, n)
    return ""


def _nome_modelo(m):
    m = (m or "").lower()
    for chave, nome in (("fable", "Fable"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")):
        if chave in m:
            return nome
    return m or "?"


def tarefa_de_subagente(caminho, tab=None, agora=None):
    """Campos da tarefa de um subagente a partir do transcript (cacheado) e do .meta.json."""
    agora = agora or time.time()
    tab = tab or custo.carregar()
    d = transcritos.dados_arquivo(caminho, "claude")
    meta = _meta(caminho)
    tot = {}
    for ms in d["dias"].values():
        for modelo, v in ms.items():
            a = tot.setdefault(modelo, {"i": 0, "o": 0, "cr": 0, "cw": 0})
            for k in a:
                a[k] += v[k]
    brl, sem_preco = 0.0, False
    for modelo, v in tot.items():
        c = custo.custo_brl(modelo, v["i"], v["o"], v["cr"], tab, v["cw"])
        sem_preco = sem_preco or c is None
        brl += c or 0.0
    modelo = d.get("modelo") if d.get("modelo") != "?" else (meta.get("pickedEffortModel") or meta.get("model") or "?")
    ini = d.get("inicio") or os.path.getmtime(caminho)
    fim = d.get("fim") or os.path.getmtime(caminho)
    parado = agora - os.path.getmtime(caminho) > ATIVO_S
    resultado = d.get("resultado") or ""
    if meta.get("finished") or (parado and resultado):
        estado = "feita"
    elif parado:
        estado, resultado = "bloqueada", resultado or "BLOQUEADA: parou sem resposta final"
    else:
        estado = "pega"
    titulo = ocultar(meta.get("description") or "", 100) or _primeira_linha(d.get("prompt1")) or "(subagente sem descrição)"
    return {"ref": "sub:" + d["sessao"], "titulo": titulo, "estado": estado, "resultado": resultado,
            "iniciado": ini, "concluido": fim if estado != "pega" else None, "atualizado": fim,
            "modelo": modelo, "nivel": coletores._nivel_modelo(modelo), "executor": "Claude %s (subagente)" % _nome_modelo(modelo),
            "projeto": d.get("projeto", ""), "tokens_in": sum(v["i"] + v["cw"] for v in tot.values()), "tokens_out": sum(v["o"] for v in tot.values()),
            "tokens_cache": sum(v["cr"] for v in tot.values()), "custo_brl": None if sem_preco and not brl else round(brl, 4),
            "sessao_mae": os.path.basename(os.path.dirname(os.path.dirname(caminho))), "agente": "subagente:" + d["sessao"][-8:]}


def tarefa_de_codex(caminho, fatias, valor_semanal, agora=None):
    agora = agora or time.time()
    d = transcritos.dados_arquivo(caminho, "codex")
    ini = d.get("inicio_ep") or os.path.getmtime(caminho)
    fim = d.get("fim_ep") or os.path.getmtime(caminho)
    parado = agora - os.path.getmtime(caminho) > ATIVO_S
    pts = fatias.get(d["sessao"], d.get("pct", 0.0))
    tk = d.get("tokens") or {}
    luna = "luna" in (d.get("modelo") or "")
    return {"ref": "codex:" + d["sessao"], "titulo": d.get("titulo") or d.get("projeto") or "(Codex sem título)",
            "estado": "feita" if parado else "pega", "resultado": d.get("resultado") or "", "iniciado": ini,
            "concluido": fim if parado else None, "atualizado": fim, "modelo": d.get("modelo", "?"), "nivel": 1 if luna else 2,
            "executor": "Codex %s%s" % (d.get("modelo", ""), "" if d.get("originator") == "codex_exec" else " (app/CLI)"),
            "projeto": d.get("projeto", ""), "tokens_in": tk.get("i", 0) + tk.get("cw", 0), "tokens_out": tk.get("o", 0),
            "tokens_cache": tk.get("cr", 0), "custo_brl": round(pts / 100.0 * valor_semanal, 4), "sessao_mae": "",
            "agente": "codex:" + d["sessao"][-8:], "_inicio": ini, "_pts": pts}


def sincronizar(dias=35, agora=None):
    """Cria/atualiza as tarefas automáticas. Devolve {'novas': n, 'atualizadas': n, 'subagentes': n, 'codex': n}."""
    from . import custos
    agora = agora or time.time()
    tab = custo.carregar()
    planos = custo.carregar_config("planos.json", {})
    semanal = (planos.get("codex") or {}).get("valor_semanal_brl", 0.0)
    itens = []
    for p in _sub_arquivos(dias):
        try:
            itens.append(tarefa_de_subagente(p, tab, agora))
        except (OSError, KeyError, ValueError):
            continue
    cdx = []
    corte = agora - dias * 86400
    for p in coletores._rollouts():
        try:
            if os.path.getmtime(p) >= corte:
                cdx.append(p)
        except OSError:
            pass
    fatias = custos.fatia_por_sessao() if cdx else {}
    with db.conectar() as con:
        lancadas = [(r["iniciado"], r["id"], r["tokens_in"] + r["tokens_out"]) for r in con.execute(
            "SELECT id,iniciado,tokens_in,tokens_out FROM tarefas WHERE auto=0 AND (agente LIKE 'codex-O%' OR executor LIKE 'Codex%') AND iniciado IS NOT NULL")]
    for p in cdx:
        try:
            t = tarefa_de_codex(p, fatias, semanal, agora)
        except (OSError, KeyError, ValueError):
            continue
        dono = next((x for x in lancadas if abs(x[0] - t["_inicio"]) <= 180), None)
        if dono:                      # já é uma tarefa do `orq lancar`: só completa os tokens/custo dela
            with db.conectar() as con:
                con.execute("UPDATE tarefas SET tokens_in=?, tokens_out=?, tokens_cache=?, custo_brl=? WHERE id=? AND tokens_in+tokens_out=0",
                            (t["tokens_in"], t["tokens_out"], t["tokens_cache"], t["custo_brl"], dono[1]))
            continue
        itens.append(t)
    novas = atualizadas = 0
    with db.conectar() as con:
        atuais = {r["ref"]: dict(r) for r in con.execute("SELECT * FROM tarefas WHERE auto=1")}
        for t in itens:
            a = atuais.get(t["ref"])
            if not a:
                cols = ["ref", "auto", "criado", "agente", "tipo"] + list(CAMPOS)
                vals = [t["ref"], 1, t["iniciado"], t["agente"], cerebro.tipo_de(t["titulo"])] + [t.get(c) for c in CAMPOS]
                con.execute("INSERT INTO tarefas(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))), vals)
                novas += 1
            elif any(a.get(c) != t.get(c) for c in CAMPOS):
                con.execute("UPDATE tarefas SET %s WHERE id=?" % ",".join("%s=?" % c for c in CAMPOS), [t.get(c) for c in CAMPOS] + [a["id"]])
                atualizadas += 1
        if novas:
            db.evento(con, "executores", "%d tarefa(s) automática(s) dos executores (subagentes/Codex)" % novas)
    return {"novas": novas, "atualizadas": atualizadas, "subagentes": sum(1 for t in itens if t["ref"].startswith("sub:")),
            "codex": sum(1 for t in itens if t["ref"].startswith("codex:"))}


def resumo_papel(rows):
    """Custo equivalente por papel: orquestrador (sessões N3, fora dos subagentes) × executores (subagentes, Sonnet/Haiku, Codex)."""
    r = {"orquestrador": {"brl": 0.0, "sessoes": set()}, "executores": {"brl": 0.0, "sessoes": set()}}
    for x in rows:
        if x["fonte"] not in ("claude", "codex"):
            continue
        k = "orquestrador" if x.get("orquestrador") else "executores"
        r[k]["brl"] += x["brl"]
        r[k]["sessoes"].add(x["sessao"])
    return {k: {"brl": round(v["brl"], 2), "sessoes": len(v["sessoes"])} for k, v in r.items()}
