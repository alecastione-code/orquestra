"""Módulo de custos: Claude (tokens × preços), Codex (fatia da cota × plano), máquina e CI (kWh × tarifa), orquestrador à parte.
Tudo é ESTIMATIVA e só guarda contagens e valores."""
import calendar
import csv
import io
import time
from datetime import datetime, timedelta

from . import custo, db, executores, sellerturbo, transcritos

POR = ("modelo", "projeto", "tarefa", "agente")


def _intervalo(periodo, agora=None):
    hoje = (agora or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    dias = {"dia": 1, "semana": 7, "mes": 30}.get(periodo)
    if not dias:
        raise ValueError("período deve ser dia, semana ou mes")
    return [(hoje - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(dias - 1, -1, -1)]


def _eh_orquestrador(modelo):
    m = (modelo or "").lower()
    return "opus" in m or "fable" in m


def _mapa_tarefas():
    """sessão (8 primeiros) → tarefa do quadro, pelo pulso/claim (agente `claude:<id8>`/id8) ou pelo log vinculado."""
    mapa = {}
    for t in db.tarefas():
        if t.get("auto"):
            continue
        for chave in (t["agente"] or "", (t["log"] or "")):
            if chave.startswith("claude:"):
                mapa[chave[7:15]] = t
            elif len(chave) >= 8 and chave.count("-") == 0 and chave not in mapa and "/" not in chave:
                mapa.setdefault(chave[:8], t)
            elif "/" in chave:
                mapa.setdefault(chave.rsplit("/", 1)[-1][:8], t)
    return mapa


def _alocar_codex(cdx):
    """A % da cota é global: o consumo da janela é repartido entre as sessões por peso de tokens (preenche d["pct"])."""
    peso = lambda d: d["tokens"]["i"] + 4 * d["tokens"]["o"] + 0.1 * d["tokens"]["cr"] + 1
    janelas = {}
    for d in cdx:
        if d.get("janela") and d.get("p_ini") is not None:
            j = janelas.setdefault(d["janela"], {"ini": 1e9, "fim": 0.0, "peso": 0.0})
            j["ini"], j["fim"], j["peso"] = min(j["ini"], d["p_ini"]), max(j["fim"], d["p_fim"]), j["peso"] + peso(d)
    for d in cdx:
        j = janelas.get(d.get("janela"))
        d["pct"] = round((j["fim"] - j["ini"]) * peso(d) / j["peso"], 2) if j and j["peso"] else 0.0
    return cdx


_fatias = {"t": 0, "v": {}}


def fatia_por_sessao(max_idade=60):
    """{sessão do Codex: pontos da cota semanal atribuídos a ela} (cache curto)."""
    if time.time() - _fatias["t"] < max_idade:
        return _fatias["v"]
    v = {d["sessao"]: d["pct"] for d in _alocar_codex(transcritos.carregar("codex"))}
    _fatias.update(t=time.time(), v=v)
    return v


def linhas(dias_set, tab=None):
    """Linhas de custo (fonte, dia, sessão, projeto, tarefa, modelo, tokens, R$) do período."""
    tab, planos = tab or custo.carregar(), custo.carregar_config("planos.json", {})
    tarefas, out = _mapa_tarefas(), []
    por_ref = {t["ref"]: t for t in db.tarefas() if t.get("ref")}      # tarefas automáticas dos executores
    for d in transcritos.carregar("claude"):
        t = por_ref.get("sub:" + d["sessao"]) if d["subagente"] else tarefas.get(d["sessao"][:8])
        for dia, ms in d["dias"].items():
            if dia not in dias_set:
                continue
            for modelo, v in ms.items():
                brl = custo.custo_brl(modelo, v["i"], v["o"], v["cr"], tab, v["cw"])
                out.append({"fonte": "claude", "dia": dia, "sessao": d["sessao"], "agente": "claude:" + d["sessao"][:8], "projeto": d["projeto"],
                            "subagente": d["subagente"], "tarefa_id": t["id"] if t else None, "tarefa": t["titulo"] if t else None,
                            "modelo": modelo, "tin": v["i"], "tout": v["o"], "cache_leitura": v["cr"], "cache_escrita": v["cw"],
                            "brl": brl or 0.0, "sem_preco": brl is None, "cota_pct": 0.0,
                            "orquestrador": _eh_orquestrador(modelo) and not d["subagente"]})   # papel: orquestrador = sessão N3; subagente é sempre executor
    semanal = (planos.get("codex") or {}).get("valor_semanal_brl", 0.0)
    cdx = _alocar_codex(transcritos.carregar("codex"))
    for d in cdx:
        if d["dia"] not in dias_set:
            continue
        v = d["tokens"]
        tc = por_ref.get("codex:" + d["sessao"])
        out.append({"fonte": "codex", "dia": d["dia"], "sessao": d["sessao"], "agente": "codex:" + d["sessao"][-8:], "projeto": d["projeto"],
                    "subagente": False, "tarefa_id": tc["id"] if tc else None, "tarefa": tc["titulo"] if tc else None, "modelo": d["modelo"], "tin": v["i"], "tout": v["o"],
                    "cache_leitura": v["cr"], "cache_escrita": v["cw"], "cota_pct": d["pct"], "janela": d.get("janela"),
                    "brl": round(d["pct"] / 100.0 * semanal, 2), "tokens_brl_ref": custo.custo_brl(d["modelo"], v["i"], v["o"], v["cr"], tab, v["cw"]),
                    "orquestrador": False, "sem_preco": False})
    en = custo.carregar_config("energia.json", {"watts_por_nucleo": 10.0, "watts_runner": 60.0, "tarifa_kwh_brl": 0.95})
    with db.conectar() as con:
        for r in con.execute("SELECT dia,chave,tarefa_id,cpu_s FROM custos_maquina"):
            if r["dia"] in dias_set:
                kwh = r["cpu_s"] * en["watts_por_nucleo"] / 3.6e6
                out.append({"fonte": "maquina", "dia": r["dia"], "sessao": r["chave"], "agente": r["chave"], "projeto": "", "subagente": False,
                            "tarefa_id": r["tarefa_id"], "tarefa": None, "modelo": "(máquina)", "tin": 0, "tout": 0, "cache_leitura": 0, "cache_escrita": 0,
                            "cota_pct": 0.0, "cpu_s": round(r["cpu_s"], 1), "kwh": round(kwh, 5), "brl": round(kwh * en["tarifa_kwh_brl"], 4),
                            "orquestrador": False, "sem_preco": False})
    ci = sellerturbo.ci_minutos()
    for dia, minutos in (ci or {}).items():
        if dia in dias_set:
            kwh = minutos / 60.0 * en["watts_runner"] / 1000.0
            out.append({"fonte": "ci", "dia": dia, "sessao": "ci:" + dia, "agente": "runner-local", "projeto": "ci", "subagente": False, "tarefa_id": None,
                        "tarefa": None, "modelo": "(CI)", "tin": 0, "tout": 0, "cache_leitura": 0, "cache_escrita": 0, "cota_pct": 0.0,
                        "minutos": round(minutos, 1), "kwh": round(kwh, 4), "brl": round(kwh * en["tarifa_kwh_brl"], 4), "orquestrador": False, "sem_preco": False})
    # nome da tarefa para linhas só com id (máquina)
    nomes = {t["id"]: t["titulo"] for t in db.tarefas()}
    for r in out:
        if r["tarefa_id"] and not r["tarefa"]:
            r["tarefa"] = nomes.get(r["tarefa_id"])
    return out


def _agrupa(rows, chave):
    g = {}
    for r in rows:
        k = {"modelo": r["modelo"], "projeto": r["projeto"] or "(sem projeto)", "agente": r["agente"],
             "tarefa": ("#%d %s" % (r["tarefa_id"], (r["tarefa"] or "")[:60])) if r["tarefa_id"] else "(sem tarefa)"}[chave]
        a = g.setdefault(k, {"chave": k, "brl": 0.0, "tin": 0, "tout": 0, "cache_leitura": 0, "cache_escrita": 0, "cota_pct": 0.0, "linhas": 0})
        a["brl"] += r["brl"]
        for c in ("tin", "tout", "cache_leitura", "cache_escrita", "cota_pct"):
            a[c] += r[c]
        a["linhas"] += 1
    return sorted(({**a, "brl": round(a["brl"], 2), "cota_pct": round(a["cota_pct"], 1)} for a in g.values()), key=lambda x: -x["brl"])


def relatorio(periodo="dia", por="modelo", agora=None):
    if por not in POR:
        raise ValueError("--por deve ser um de: %s" % ", ".join(POR))
    agora_dt = agora or datetime.now()
    dias = _intervalo(periodo, agora_dt)
    tab, planos = custo.carregar(), custo.carregar_config("planos.json", {})
    rows = linhas(set(dias), tab)
    partes = {f: round(sum(r["brl"] for r in rows if r["fonte"] == f), 2) for f in ("claude", "codex", "maquina", "ci")}
    por_dia = []
    for d in dias:
        x = {f: round(sum(r["brl"] for r in rows if r["dia"] == d and r["fonte"] == f), 2) for f in partes}
        por_dia.append(dict(x, dia=d, total=round(sum(x.values()), 2)))
    tarefas = [a for a in _agrupa([r for r in rows if r["tarefa_id"]], "tarefa")][:10]
    # economia vs "tudo no modelo do orquestrador" (todo o trabalho dos executores)
    mo = tab.get("modelo_orquestrador", "claude-opus-5-5")
    econ = 0.0
    for r in rows:
        if r["fonte"] in ("claude", "codex") and not r["orquestrador"] and not r["sem_preco"]:      # executores: subagentes, Sonnet/Haiku e Codex
            teria = custo.custo_brl(mo, r["tin"], r["tout"], r["cache_leitura"], tab, r["cache_escrita"]) or 0.0
            econ += max(0.0, teria - r["brl"])
    # orquestrador à parte: por dia e por PR publicado
    prs = sellerturbo.prs_mesclados()
    orq_dia = {}
    for r in rows:
        if r["orquestrador"]:
            orq_dia[r["dia"]] = orq_dia.get(r["dia"], 0.0) + r["brl"]
    orquestrador = [{"dia": d, "brl": round(v, 2), "prs": (prs or {}).get(d), "brl_por_pr": round(v / prs[d], 2) if prs and prs.get(d) else None}
                    for d, v in sorted(orq_dia.items())]
    total = round(sum(partes.values()), 2)
    rel = {"periodo": periodo, "dias": dias, "por": por, "estimativa": True, "total_brl": total, "partes": partes,
           "agrupado": _agrupa(rows, por), "por_dia": por_dia, "top_tarefas": tarefas, "economia_vs_orquestrador": round(econ, 2),
           "orquestrador": orquestrador, "por_papel": executores.resumo_papel(rows), "custo_por_pr": round(total / sum(prs.get(d, 0) for d in dias), 2) if prs and sum(prs.get(d, 0) for d in dias) else None,
           "maquina": {"kwh": round(sum(r.get("kwh", 0) for r in rows if r["fonte"] in ("maquina", "ci")), 3),
                       "cpu_s": round(sum(r.get("cpu_s", 0) for r in rows if r["fonte"] == "maquina"))},
           "codex": _codex_resumo(rows, partes["codex"]),
           "claude_equivalente_api": True,
           "rotulo_claude": "equivalente a preço de API (o plano do dono não é cobrado assim)",
           "tokens": {k: sum(r[k] for r in rows if r["fonte"] in ("claude", "codex")) for k in ("tin", "tout", "cache_leitura", "cache_escrita")},
           "sem_preco": sorted({r["modelo"] for r in rows if r["sem_preco"]}),
           "precos_vigencia": tab.get("vigencia"), "planos_vigencia": planos.get("vigencia")}
    rel.update(previsao(agora_dt, tab, planos))
    return rel


def _janela_atual(rows=None):
    renova = db.ultima_renovacao("codex")
    if renova:
        return int(renova)
    js = [r.get("janela") for r in (rows or []) if r.get("janela")]
    return max(js) if js else None


def _codex_resumo(rows, brl):
    """cota_pct = fatia do CICLO ATUAL (nunca passa de 100); pontos_periodo = soma de pontos de todos os ciclos, sem %."""
    cx = [r for r in rows if r["fonte"] == "codex"]
    atual = _janela_atual(cx)
    return {"cota_pct": round(min(100.0, sum(r["cota_pct"] for r in cx if r.get("janela") == atual)), 1),
            "pontos_periodo": round(sum(r["cota_pct"] for r in cx), 1), "brl": brl}


def ritmo_codex_raw(agora_ts=None):
    """Pontos/dia do Codex nas últimas 24 h pela série dos rollouts (alimenta db.cotas); None sem série."""
    return ritmo_codex(agora_ts)[0]


def ritmo_codex(agora_ts=None):
    """Pontos/dia nas últimas 24 h e na média do ciclo, pela série de leituras do ciclo atual (rollouts)."""
    agora_ts = agora_ts or time.time()
    atual = _janela_atual()
    amostras = sorted((a for d in transcritos.carregar("codex") if d.get("janela") == atual for a in d.get("serie", []) if a[2] == atual),
                      key=lambda a: a[0])
    if len(amostras) < 2:
        return None, None, {}
    fim = amostras[-1]
    ref = next((a for a in reversed(amostras) if a[0] <= agora_ts - 86400), amostras[0])
    span = max(fim[0] - ref[0], 3600.0)
    r24 = max(0.0, fim[1] - ref[1]) * 86400.0 / span
    ini = amostras[0]
    ciclo = max(0.0, fim[1] - ini[1]) * 86400.0 / max(fim[0] - ini[0], 3600.0)
    ref6 = next((a for a in reversed(amostras) if a[0] <= agora_ts - 6 * 3600), amostras[0])
    r6 = max(0.0, fim[1] - ref6[1]) * 86400.0 / max(fim[0] - ref6[0], 3600.0)
    ultimo_aumento = next((a for a in reversed(amostras) if a[1] < fim[1]), None)
    extra = {"ritmo_6h_pts_dia": round(r6, 1), "pts_24h": round(max(0.0, fim[1] - ref[1]), 1),
             "horas_sem_consumo": round((agora_ts - (amostras[amostras.index(ultimo_aumento) + 1][0] if ultimo_aumento else amostras[0][0])) / 3600, 1)}
    return round(r24, 1), round(ciclo, 1), extra


def previsao(agora=None, tab=None, planos=None):
    """Claude: só o equivalente a preço de API (R$ não é limite). Limites e alertas vêm das janelas informadas pelas IAs."""
    from . import cotas as cotas_mod
    agora = agora or datetime.now()
    mes = agora.strftime("%Y-%m")
    dias_mes = calendar.monthrange(agora.year, agora.month)[1]
    rows = [r for r in linhas({"%s-%02d" % (mes, d) for d in range(1, dias_mes + 1)}) if r["fonte"] == "claude"]
    gasto_mes = sum(r["brl"] for r in rows)
    cs = db.cotas()
    prev = {"claude": {"gasto_mes_equivalente_api": round(gasto_mes, 2), "equivalente_api": True},
            "janelas": [{"agente": c["agente"], "nome": cotas_mod.nome(c["agente"]), "usado_pct": c["usado_pct"], "disponivel_pct": c["disponivel_pct"],
                         "renova_em": c["renova_em"], "horas_ate_renovar": c["horas_ate_renovar"], "ritmo_atual": c["ritmo_atual"],
                         "ritmo_permitido": c["ritmo_permitido"], "ritmo_unidade": c["ritmo_unidade"], "previsao_fim_pct": c["previsao_fim_pct"],
                         "estoura": c["estoura"], "desatualizada": c["desatualizada"], "origem": c["origem"], "confirmado_pct": c["confirmado_pct"],
                         "idade_leitura_s": c["idade_leitura_s"], "leitura_velha": c["leitura_velha"]} for c in cs]}
    c = next((x for x in cs if x["agente"] == "codex"), None)
    if c:
        r24, rciclo, extra = ritmo_codex(agora.timestamp())
        prev["codex"] = {"usado_pct": c["usado_pct"], "ritmo_24h_pts_dia": r24, "ritmo_ciclo_pts_dia": rciclo, "ritmo_permitido_pts_dia": c["ritmo_permitido"],
                         "projecao_fim_ciclo_pct": c["previsao_fim_pct"], "dias_ate_renovar": c["dias_ate_renovar"], "estoura": c["estoura"], **extra}
    return {"previsao": prev, "alertas": cotas_mod.alertas(cs)}


# ------------------------------------------------------------------ exportação
def csv_texto(rel):
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(["periodo", rel["periodo"], "estimativa", "sim"])
    w.writerow([rel["por"], "brl", "tokens_entrada", "tokens_saida", "cache_leitura", "cache_escrita", "cota_codex_pct"])
    for a in rel["agrupado"]:
        w.writerow([a["chave"], "%.2f" % a["brl"], a["tin"], a["tout"], a["cache_leitura"], a["cache_escrita"], a["cota_pct"]])
    w.writerow([])
    w.writerow(["dia", "claude", "codex", "maquina", "ci", "total"])
    for d in rel["por_dia"]:
        w.writerow([d["dia"], d["claude"], d["codex"], d["maquina"], d["ci"], d["total"]])
    return s.getvalue()


def markdown(rel):
    L = ["# Custos — %s (estimativa)" % rel["periodo"], "",
         "**Total (equivalente a preço de API): R$ %.2f** · Claude R$ %.2f (%s) · Codex R$ %.2f (%.1f%% do ciclo atual; %.1f pts no período) · máquina R$ %.2f · CI R$ %.2f" % (
             rel["total_brl"], rel["partes"]["claude"], rel["rotulo_claude"], rel["partes"]["codex"], rel["codex"]["cota_pct"], rel["codex"]["pontos_periodo"], rel["partes"]["maquina"], rel["partes"]["ci"]),
         "Economia vs. tudo no modelo do orquestrador: R$ %.2f" % rel["economia_vs_orquestrador"],
         "Preços vigentes desde %s; planos desde %s (edite `config/`)." % (rel["precos_vigencia"], rel["planos_vigencia"]), ""]
    for a in rel["alertas"]:
        L.append("> ⚠ " + a)
    L += ["", "## Por %s" % rel["por"], "", "| %s | R$ | entrada | saída | cache leit. | cache esc. |" % rel["por"], "|---|---|---|---|---|---|"]
    for a in rel["agrupado"][:25]:
        L.append("| %s | %.2f | %d | %d | %d | %d |" % (a["chave"], a["brl"], a["tin"], a["tout"], a["cache_leitura"], a["cache_escrita"]))
    L += ["", "## 10 tarefas mais caras", ""]
    for a in rel["top_tarefas"]:
        L.append("- %s — R$ %.2f" % (a["chave"], a["brl"]))
    pc = rel["previsao"]["claude"]
    L += ["", "## Janelas das IAs (informadas por elas)", "",
          "- Claude: R$ %.2f no mês, equivalente a preço de API (não é limite nem cobrança)" % pc["gasto_mes_equivalente_api"]]
    for j in rel["previsao"]["janelas"]:
        L.append("- %s: %s%% usado, %s%% disponível, ritmo %s %s (permitido %s), previsão %s%% ao renovar%s" % (
            j["nome"], j["usado_pct"], j["disponivel_pct"], j["ritmo_atual"], j["ritmo_unidade"], j["ritmo_permitido"], j["previsao_fim_pct"],
            " — DESATUALIZADA, peça à IA para informar" if j["desatualizada"] else ""))
    return "\n".join(L) + "\n"
