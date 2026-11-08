"""Custo por skill: custo de CARREGAR (texto da skill no contexto) × custo do TRABALHO GUIADO por ela. Estimativa."""
import time
from datetime import datetime, timedelta

from . import custo, custos, db, transcritos

TAM_GRANDE = 12000   # SKILL.md acima disso e usada várias vezes: dividir em references/
_cache = {"k": None, "t": 0, "v": None}


def _agregar(dias_set, tab):
    tarefas = custos._mapa_tarefas()
    agg = {}
    for d in transcritos.carregar("claude"):
        t = tarefas.get(d["sessao"][:8])
        for e in d["skills"]:
            if e["dia"] not in dias_set:
                continue
            a = agg.setdefault(e["skill"], {"skill": e["skill"], "usos": 0, "carga_tokens": 0, "carga_brl": 0.0, "guiado_tokens": 0, "guiado_brl": 0.0,
                                            "abandonadas": 0, "via_dono": 0, "repetidos": 0, "sessoes": set(), "tarefas": {}})
            a["usos"] += 1
            a["carga_tokens"] += e["carga_tokens"]
            modelos = e["g"] or {}
            ref = max(modelos, key=lambda m: modelos[m]["o"]) if modelos else tab.get("modelo_orquestrador", "claude-opus-5-5")
            a["carga_brl"] += custo.custo_brl(ref, e["carga_tokens"], 0, 0, tab) or 0.0
            for m, v in modelos.items():
                a["guiado_tokens"] += v["i"] + v["o"] + v["cr"] + v["cw"]
                a["guiado_brl"] += custo.custo_brl(m, v["i"], v["o"], v["cr"], tab, v["cw"]) or 0.0
            a["abandonadas"] += 1 if e["abandonada"] else 0
            a["via_dono"] += 1 if e["modo"] == "comando-dono" else 0
            if d["sessao"] not in a["sessoes"]:
                a["sessoes"].add(d["sessao"])
                a["repetidos"] += d["repetidos"].get(e["skill"], 0)
                if t:
                    a["tarefas"][t["id"]] = t
    return agg


def _recomendar(a, mediana, pct):
    sem_resultado = a["resultados"] == 0
    if a["usos"] == 0:
        return "manter", "sem uso no período: custo zero (ocupa só a lista)"
    if pct >= 10 and sem_resultado and a["via_dono"] == 0:
        return "pausar", "%.0f%% do gasto e nenhum resultado associado (e o dono não a chama direto): pausar, reversível" % pct
    motivos = []
    if a["tamanho_md"] > TAM_GRANDE and a["usos"] >= 3:
        motivos.append("SKILL.md de %d KB entra inteiro toda vez: dividir em references/ sob demanda" % (a["tamanho_md"] // 1024))
    if a["repetidos"] >= 3:
        motivos.append("%d leituras repetidas de scripts/referências na mesma sessão: virar script nível 0" % a["repetidos"])
    if a["usos"] >= 3 and a["abandonadas"] / a["usos"] >= 0.5:
        motivos.append("%d de %d usos foram abandonados logo após carregar: descrição dispara errado" % (a["abandonadas"], a["usos"]))
    if a["usos"] >= 3 and mediana and a["media_por_uso"] > 2 * mediana:
        motivos.append("R$ %.2f por uso, mais que o dobro da mediana (R$ %.2f)" % (a["media_por_uso"], mediana))
    if motivos:
        return "otimizar", "; ".join(motivos) + (" · pausar é proibido automático: o dono a usa direto" if a["via_dono"] else "")
    return "manter", "custo e resultado dentro do esperado"


def relatorio(periodo="semana", agora=None):
    agora = agora or datetime.now()
    tab = custo.carregar()
    dias = set(custos._intervalo(periodo, agora))
    quatorze = {(agora - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(14)}
    total = sum(r["brl"] for r in custos.linhas(dias, tab) if r["fonte"] == "claude") or 1.0
    total14 = sum(r["brl"] for r in custos.linhas(quatorze, tab) if r["fonte"] == "claude") or 1.0
    ag, ag14 = _agregar(dias, tab), _agregar(quatorze, tab)
    saida = []
    for nome, a in ag.items():
        brl = a["carga_brl"] + a["guiado_brl"]
        feitas = [t for t in a["tarefas"].values() if t["estado"] in ("feita", "aguardando")]
        r = {"skill": nome, "usos": a["usos"], "tokens_carregar": a["carga_tokens"], "brl_carregar": round(a["carga_brl"], 2),
             "tokens_guiado": a["guiado_tokens"], "brl_guiado": round(a["guiado_brl"], 2), "brl_total": round(brl, 2),
             "media_por_uso": round(brl / a["usos"], 2), "pct_gasto": round(brl / total * 100, 1), "abandonadas": a["abandonadas"],
             "repetidos": a["repetidos"], "via_dono": a["via_dono"], "tamanho_md": transcritos._tamanho_skill(nome),
             "resultados": len(feitas), "gates_verdes": sum(1 for t in feitas if not t["gate_falhou"] and t["entrega"] != "quebrou"),
             "tarefas_associadas": len(a["tarefas"])}
        r["custo_por_resultado"] = round(brl / r["resultados"], 2) if r["resultados"] else None
        saida.append(r)
    saida.sort(key=lambda x: -x["brl_total"])
    meds = sorted(x["media_por_uso"] for x in saida)
    mediana = meds[len(meds) // 2] if meds else 0
    for r in saida:  # a recomendação usa a janela de 14 dias (duas semanas)
        b = ag14.get(r["skill"])
        if b:
            x = dict(r, usos=b["usos"], abandonadas=b["abandonadas"], repetidos=b["repetidos"], via_dono=b["via_dono"],
                     resultados=len([t for t in b["tarefas"].values() if t["estado"] in ("feita", "aguardando")]),
                     media_por_uso=(b["carga_brl"] + b["guiado_brl"]) / b["usos"])
            pct = (b["carga_brl"] + b["guiado_brl"]) / total14 * 100
        else:
            x, pct = r, r["pct_gasto"]
        r["acao"], r["porque"] = _recomendar(x, mediana, pct)
    return {"periodo": periodo, "estimativa": True, "total_claude_brl": round(total, 2), "skills": saida}


def por_nome(periodo="semana", max_idade=60):
    """Mapa nome → linha do relatório (com cache curto), para a aba Skills."""
    if _cache["k"] == periodo and time.time() - _cache["t"] < max_idade:
        return _cache["v"]
    v = {r["skill"].split(":")[-1]: r for r in relatorio(periodo)["skills"]}
    _cache.update(k=periodo, t=time.time(), v=v)
    return v


def proposta_otimizar(r):
    itens = []
    if r["tamanho_md"] > TAM_GRANDE:
        itens.append("Dividir o SKILL.md (%d KB): deixar no corpo só o essencial e mover o resto para `references/`, carregadas sob demanda." % (r["tamanho_md"] // 1024))
    if r["repetidos"] >= 2:
        itens.append("Há %d leituras repetidas de scripts/referências na mesma sessão: transformar em script nível 0 ou resumir no SKILL.md." % r["repetidos"])
    if r["usos"] and r["abandonadas"] / r["usos"] >= 0.4:
        itens.append("%d de %d usos foram abandonados logo após carregar: estreitar os gatilhos da `description`." % (r["abandonadas"], r["usos"]))
    if not itens:
        itens.append("Revisar o texto: custo de carregar R$ %.2f por uso e R$ %.2f do trabalho guiado; cortar o que o modelo já sabe." % (
            r["brl_carregar"] / max(r["usos"], 1), r["brl_guiado"] / max(r["usos"], 1)))
    return " ".join(itens)


def otimizar(nome, periodo="mes"):
    """Gera a proposta de enxugar a skill. Óbvia e segura → aplicada pela regra da auto-melhoria (cria a tarefa)."""
    from . import melhorias
    r = next((x for x in relatorio(periodo)["skills"] if x["skill"].split(":")[-1] == nome.split(":")[-1]), None)
    if not r:
        raise KeyError("sem uso da skill %r no período: nada para otimizar" % nome)
    mid = melhorias.registrar(melhorias._s("otimizar-skill:%s:%s" % (nome, datetime.now().strftime("%Y-%m-%d")),
        "Enxugar a skill %s (R$ %.2f no período, %d usos)" % (nome, r["brl_total"], r["usos"]),
        "carregar R$ %.2f + trabalho guiado R$ %.2f; %d abandonos; %d leituras repetidas; SKILL.md %d KB" % (
            r["brl_carregar"], r["brl_guiado"], r["abandonadas"], r["repetidos"], r["tamanho_md"] // 1024),
        proposta_otimizar(r), "menos tokens por uso", "baixo (edição de texto, reversível no git)", "Sonnet", "baixo", 4.0 + r["pct_gasto"]), "painel")
    return {"melhoria": mid, "proposta": proposta_otimizar(r)}
