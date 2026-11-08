"""Linha do tempo do dia: quem fez o quê, quando, custo e resultado. Exporta em Markdown."""
from datetime import datetime

from . import custo, db


def _h(t):
    return datetime.fromtimestamp(t).strftime("%H:%M") if t else "—"


def _dur(s):
    return "—" if not s else ("%ds" % s if s < 60 else "%dmin" % (s // 60) if s < 3600 else "%dh%02d" % (s // 3600, s % 3600 // 60))


def dia(dia=None):
    dia = dia or datetime.now().strftime("%Y-%m-%d")
    d = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else None
    tab, itens = custo.carregar(), []
    for t in db.tarefas():
        if dia not in (d(t["iniciado"]), d(t["concluido"]), d(t["criado"])):
            continue
        c = custo.resumo(t, tab)
        itens.append({"quando": t["iniciado"] or t["criado"], "tipo": "tarefa", "id": t["id"], "agente": t["agente"] or t["executor"] or "—",
                      "nivel": t["nivel"], "titulo": t["titulo"], "estado": t["estado"], "inicio": t["iniciado"], "fim": t["concluido"],
                      "duracao_s": int(t["concluido"] - t["iniciado"]) if t["iniciado"] and t["concluido"] else None,
                      "custo": c["texto"], "economia": c["economia"], "entrega": t["entrega"], "resultado": (t["resultado"] or "")[:300]})
    for e in db.eventos(500):
        if d(e["quando"]) == dia and e["tipo"] in ("alerta", "acao", "desperdicio", "gate", "vigia", "fila"):
            itens.append({"quando": e["quando"], "tipo": "evento:" + e["tipo"], "titulo": e["texto"]})
    itens.sort(key=lambda i: i["quando"])
    return {"dia": dia, "itens": itens, "economia": custo.economia_dia(dia)}


def markdown(dia_=None):
    r = dia(dia_)
    L = ["# Linha do tempo — %s" % r["dia"], "",
         "Economia estimada no dia: R$ %.2f em %d tarefa(s) (estimativa; ver `config/precos.json`)." % (r["economia"]["valor"], r["economia"]["tarefas"]), ""]
    for i in r["itens"]:
        if i["tipo"] == "tarefa":
            L.append("- **%s** · N%d · %s — #%d %s → %s%s" % (_h(i["quando"]), i["nivel"], i["agente"], i["id"], i["titulo"], i["estado"],
                     " (%s)" % i["entrega"] if i["entrega"] else ""))
            L.append("  - duração: %s · %s" % (_dur(i["duracao_s"]), i["custo"]))
            if i["resultado"]:
                L.append("  - resultado: %s" % " ".join(i["resultado"].split()))
        else:
            L.append("- **%s** · %s — %s" % (_h(i["quando"]), i["tipo"].split(":")[1], i["titulo"]))
    if not r["itens"]:
        L.append("_Nada registrado neste dia._")
    return "\n".join(L) + "\n"
