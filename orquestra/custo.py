"""Custo estimado em R$ por tarefa (tokens × tabela editável em config/precos.json) + fatia da cota do Codex."""
import json
import os
import time
from datetime import datetime

from . import config, db

PADRAO = {"cambio_brl": 5.5, "modelo_orquestrador": "claude-opus-5-5", "cache_leitura_fracao": 0.1,
          "modelos": {"claude-haiku-5-5": {"entrada": 1.0, "saida": 5.0}, "claude-sonnet-5-5": {"entrada": 3.0, "saida": 15.0},
                      "claude-opus-5-5": {"entrada": 5.0, "saida": 25.0}, "claude-fable-5-1": {"entrada": 15.0, "saida": 75.0}}}


def _pastas():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(config.casa_orquestra(), "config"), os.path.join(raiz, "config")]


def carregar_config(nome, padrao):
    """config/<nome>.json — a cópia em Application Support/Orquestra/config sobrepõe a do repositório."""
    for pasta in _pastas():
        p = os.path.join(pasta, nome)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
    return padrao


def carregar():
    if os.environ.get("ORQUESTRA_PRECOS"):
        cand = [os.environ["ORQUESTRA_PRECOS"]]
    else:
        cand = [os.path.join(p, "precos.json") for p in _pastas()]
    for p in cand:
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                pass
    return PADRAO


def preco(modelo, tab=None):
    tab = tab or carregar()
    ms = tab.get("modelos", {})
    if modelo in ms:
        return ms[modelo]
    for k, v in ms.items():
        if modelo and (k in modelo or modelo in k):
            return v
    return None


def custo_brl(modelo, entrada, saida, cache=0, tab=None, cache_escrita=0):
    """Tokens × tabela. `cache` = leitura de cache; `cache_escrita` à parte (preços distintos, com padrão 10% / 125% da entrada)."""
    tab = tab or carregar()
    p = preco(modelo, tab)
    if not p:
        return None
    pl = p.get("cache_leitura", p["entrada"] * tab.get("cache_leitura_fracao", 0.1))
    pe = p.get("cache_escrita", p["entrada"] * 1.25)
    usd = (entrada * p["entrada"] + saida * p["saida"] + cache * pl + cache_escrita * pe) / 1e6
    return round(usd * tab.get("cambio_brl", 5.5), 2)


def resumo(t, tab=None):
    """Custo da tarefa: custou X / teria custado Y no modelo do orquestrador / economia. Sempre estimativa."""
    tab = tab or carregar()
    ent, sai = t.get("tokens_in") or 0, t.get("tokens_out") or 0
    fim = t.get("concluido") or time.time()
    fatia = None
    if not t.get("auto") and t.get("iniciado") and (t.get("modelo", "").startswith("gpt") or "codex" in (t.get("agente") or "") or "codex" in (t.get("executor") or "").lower()):
        fatia = db.cota_consumo("codex", t["iniciado"], fim)
    r = {"estimativa": True, "tokens_in": ent, "tokens_out": sai, "fatia_cota_codex_pts": fatia,
         "custou": None, "teria_custado": None, "economia": None, "modelo_orquestrador": tab.get("modelo_orquestrador")}
    if (ent or sai or t.get("custo_brl") is not None) and t.get("modelo"):
        cache = t.get("tokens_cache") or t.get("cache", 0)
        r["custou"] = t["custo_brl"] if t.get("custo_brl") is not None else custo_brl(t["modelo"], ent, sai, cache, tab)
        r["teria_custado"] = custo_brl(tab.get("modelo_orquestrador", "claude-opus-5-5"), ent, sai, cache, tab)
        if r["custou"] is not None and r["teria_custado"] is not None:
            r["economia"] = round(r["teria_custado"] - r["custou"], 2)
    partes = []
    if r["custou"] is not None and r["teria_custado"] is not None:
        partes.append("custou R$ %.2f (estimativa) / teria custado R$ %.2f no %s" % (r["custou"], r["teria_custado"], r["modelo_orquestrador"]))
    elif r["custou"] is not None:
        partes.append("custou R$ %.2f (equivalente, estimativa)" % r["custou"])
    if fatia is not None:
        partes.append("fatia da cota do Codex: %.1f pts" % fatia)
    r["texto"] = "; ".join(partes) or "custo indisponível (sem tokens informados)"
    return r


def economia_dia(dia=None):
    """Soma da economia estimada das tarefas concluídas hoje."""
    dia = dia or datetime.now().strftime("%Y-%m-%d")
    tab, total, n = carregar(), 0.0, 0
    for t in db.tarefas():
        if t["concluido"] and datetime.fromtimestamp(t["concluido"]).strftime("%Y-%m-%d") == dia and t["estado"] in ("feita", "aguardando"):
            e = resumo(t, tab)["economia"]
            if e:
                total += e
                n += 1
    return {"valor": round(total, 2), "tarefas": n, "estimativa": True}
