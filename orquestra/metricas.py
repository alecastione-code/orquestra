"""'O que medir' (references/economia-e-qualidade.md, seção 4): cartões com tendência semanal (4 semanas)."""
import glob
import os
import re
from datetime import datetime, timedelta

from . import config, custo, custos, db, sellerturbo, transcritos


def _semanas(n=4, agora=None):
    """Lista de n semanas (mais antiga → atual), cada uma {rotulo, dias:set}."""
    hoje = (agora or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for w in range(n - 1, -1, -1):
        fim = hoje - timedelta(days=7 * w)
        dias = {(fim - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(7)}
        out.append({"rotulo": "S-%d" % w if w else "esta", "dias": dias, "ini": fim - timedelta(days=6), "fim": fim + timedelta(days=1)})
    return out


def _dia(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else None


def _card(id_, titulo, meta, unidade, serie, melhor, limite=None, dica="", sem_dado=False):
    atual = serie[-1]
    ruim = False
    if atual is not None and limite is not None:
        ruim = atual < limite if melhor == "maior" else atual > limite
    elif atual is not None and len([x for x in serie if x is not None]) >= 2:
        ant = [x for x in serie[:-1] if x is not None][-1]
        ruim = atual > ant if melhor == "menor" else atual < ant
    return {"id": id_, "titulo": titulo, "meta": meta, "unidade": unidade, "serie": serie, "atual": atual, "melhor_se": melhor,
            "estado": "sem dado" if atual is None else "ruim" if ruim else "ok", "dica": dica}


def cartoes(agora=None):
    sem = _semanas(4, agora)
    tarefas = db.tarefas()
    tab = custo.carregar()
    claude = transcritos.carregar("claude")
    prs = sellerturbo.prs_mesclados()

    # 1. tokens do orquestrador por PR publicado
    tok_orq, prs_sem = [], []
    for s in sem:
        t = sum(v["i"] + v["o"] + v["cw"] for d in claude for dia, ms in d["dias"].items() if dia in s["dias"]
                for m, v in ms.items() if custos._eh_orquestrador(m) and not d["subagente"])
        n = sum((prs or {}).get(dia, 0) for dia in s["dias"]) if prs is not None else None
        prs_sem.append(n)
        tok_orq.append(round(t / n) if n else None)

    # 2. gate verde na 1ª tentativa (tarefas revisadas da semana; entrega 'quebrou' = gate falhou)
    gate = []
    for s in sem:
        ts = [t for t in tarefas if t["estado"] == "feita" and t["entrega"] and _dia(t["concluido"]) in s["dias"]]
        gate.append(round(100 * sum(1 for t in ts if t["entrega"] != "quebrou") / len(ts)) if ts else None)

    # 3. retrabalho (voltou da revisão) — geral e por nível
    retr, por_nivel = [], {}
    for s in sem:
        ts = [t for t in tarefas if t["entrega"] and _dia(t["concluido"] or t["atualizado"]) in s["dias"]]
        retr.append(round(100 * sum(1 for t in ts if t["voltas"] > 0) / len(ts)) if ts else None)
    for n in (1, 2, 3):
        ts = [t for t in tarefas if t["entrega"] and t["nivel"] == n]
        por_nivel[n] = round(100 * sum(1 for t in ts if t["voltas"] > 0) / len(ts)) if ts else None

    # 4. intervenções do dono perguntando estado: transcritos do orquestrador (nível 3) + mensagens do dono no quadro
    perg = []
    for s in sem:
        n = sum(v for d in claude if not d["subagente"] and custos._eh_orquestrador(d["modelo"]) for dia, v in d["perguntas"].items() if dia in s["dias"])
        n += sum(1 for m in db.msgs(limite=2000) if m["de"] == "dono" and _dia(m["criado"]) in s["dias"] and transcritos.PERGUNTA.search(m["texto"]))
        perg.append(n)

    # 5. tempo até o ar (abrir → conferido): mediana das tarefas revisadas, em minutos
    tempo = []
    for s in sem:
        ds = sorted((t["concluido"] - t["iniciado"]) / 60 for t in tarefas if t["estado"] == "feita" and t["iniciado"] and t["concluido"] and _dia(t["concluido"]) in s["dias"])
        tempo.append(round(ds[len(ds) // 2]) if ds else None)

    # 6. correções do dono → regra (memórias feedback criadas/atualizadas por semana)
    corr = []
    arquivos = []
    for p in glob.glob(os.path.join(config.claude_home(), "projects", "*", "memory", "*.md")):
        if os.path.basename(p) == "MEMORY.md":
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read(1500)
            if re.search(r"type:\s*feedback", txt):
                arquivos.append(os.path.getmtime(p))
        except OSError:
            continue
    for s in sem:
        corr.append(sum(1 for m in arquivos if _dia(m) in s["dias"]))

    return [
        _card("tokens_pr", "Tokens do orquestrador por PR publicado", "cair semana a semana", "tokens/PR", tok_orq, "menor",
              dica="delegar mais, briefs melhores" + ("" if prs is not None else " · sem dado do GitHub"), sem_dado=prs is None),
        _card("gate_1a", "Gate verde na 1ª tentativa", "≥ 80%", "%", gate, "maior", 80, "regra \"teste o que tocou\" mais estrita"),
        _card("retrabalho", "Retrabalho (voltou da revisão)", "≤ 15%", "%", retr, "menor", 15, "brief mais completo ou subir de nível esse tipo"),
        _card("perguntas", "Intervenções do dono perguntando estado", "0 por dia", "por semana", perg, "menor", 0, "vigias/avisos que faltaram"),
        _card("tempo_ar", "Tempo até o ar (abrir → conferido)", "cair", "min (mediana)", tempo, "menor", None, "fila, CI e conferência automáticos"),
        _card("correcoes", "Correções do dono viraram regra/memória", "cada uma vira regra", "memórias feedback/semana", corr, "maior", None, "auto-melhoria na hora"),
    ], {"retrabalho_por_nivel": por_nivel, "semanas": [s["rotulo"] for s in sem], "prs_por_semana": prs_sem}


def resumo(agora=None):
    cs, extra = cartoes(agora)
    return {"cartoes": cs, **extra}
