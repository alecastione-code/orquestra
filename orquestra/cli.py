"""CLI `orq`: o quadro comum para Claude, Codex e o orquestrador."""
import argparse
import json
import os
import sys
from datetime import datetime

from . import db, skills, cerebro


def _quando(t):
    return datetime.fromtimestamp(t).strftime("%d/%m %H:%M") if t else "—"


def _ler_brief(v):
    if v and os.path.isfile(v):
        with open(v, encoding="utf-8", errors="replace") as f:
            return f.read()
    return v or ""


def _eu():
    return os.environ.get("ORQ_AGENTE", "orquestrador")


def _ler_json(caminho):
    if caminho == "-":
        return json.loads(sys.stdin.read())
    with open(os.path.expanduser(caminho), encoding="utf-8") as f:
        return json.load(f)


def _dur(s):
    if s is None:
        return "—"
    return "%ds" % s if s < 60 else "%dmin" % (s // 60) if s < 3600 else "%dh%02d" % (s // 3600, s % 3600 // 60)


def _ao_vivo():
    """Pede ao servidor (tem o histórico de CPU); sem servidor, tira uma fotografia local."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:4747/api/ao-vivo", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        from . import aovivo
        return aovivo.AMOSTRADOR.amostrar()


def montar():
    p = argparse.ArgumentParser(prog="orq", description="Quadro comum de orquestração (Claude + Codex).")
    s = p.add_subparsers(dest="cmd", required=True)

    t = s.add_parser("tarefa").add_subparsers(dest="sub", required=True)
    n = t.add_parser("nova"); n.add_argument("titulo"); n.add_argument("--nivel", type=int, default=2)
    n.add_argument("--projeto", default=""); n.add_argument("--brief", default=""); n.add_argument("--executor", default="")
    n.add_argument("--tipo", default=""); n.add_argument("--quando-sobrar", "--nao-urgente", dest="quando_sobrar", action="store_true")
    g = t.add_parser("pegar"); g.add_argument("id", type=int); g.add_argument("--agente", default=None); g.add_argument("--forcar", action="store_true")
    f = t.add_parser("feita"); f.add_argument("id", type=int); f.add_argument("--resultado", default=""); f.add_argument("--agente")
    f.add_argument("--tokens-in", type=int, default=0); f.add_argument("--tokens-out", type=int, default=0); f.add_argument("--modelo")
    rv = t.add_parser("revisada"); rv.add_argument("id", type=int); rv.add_argument("--por", default="orquestrador")
    vo = t.add_parser("voltou"); vo.add_argument("id", type=int); vo.add_argument("--motivo", required=True)
    gt = t.add_parser("gate"); gt.add_argument("id", type=int); gt.add_argument("--falhou", action="store_true")
    b = t.add_parser("bloqueada"); b.add_argument("id", type=int); b.add_argument("--motivo", required=True); b.add_argument("--agente")
    e = t.add_parser("etapa"); e.add_argument("id", type=int); e.add_argument("texto")
    pz = t.add_parser("pausar"); pz.add_argument("id", type=int); pz.add_argument("--motivo", required=True)
    rt = t.add_parser("retomar"); rt.add_argument("id", type=int)
    l = t.add_parser("lista"); l.add_argument("--estado", choices=db.ESTADOS); l.add_argument("--projeto"); l.add_argument("--json", action="store_true")

    m = s.add_parser("msg"); m.add_argument("para"); m.add_argument("texto"); m.add_argument("--de", default=None)
    ms = s.add_parser("msgs"); ms.add_argument("--agente", default=None); ms.add_argument("--todas", action="store_true")

    c = s.add_parser("cota"); c.add_argument("agente", nargs="?"); c.add_argument("--usado", type=float)
    c.add_argument("--renova", default=None); c.add_argument("--janela-h", type=float, default=None); c.add_argument("--json", default=None, metavar="ARQUIVO")
    ci = s.add_parser("cotas-informar"); ci.add_argument("arquivo", help="arquivo JSON ou - para stdin: {plan:{windows:[{label,percentUsed,resetsAt}]}}")
    s.add_parser("cotas")

    pl = s.add_parser("pulso"); pl.add_argument("--agente", default=None); pl.add_argument("nota", nargs="?", default="")

    pj = s.add_parser("projeto").add_subparsers(dest="sub", required=True)
    pn = pj.add_parser("novo"); pn.add_argument("nome"); pn.add_argument("--descricao", default="")
    pj.add_parser("lista")

    pr = s.add_parser("processo"); pr.add_argument("acao", choices=["pausar", "retomar", "encerrar"]); pr.add_argument("pid"); pr.add_argument("--sim", action="store_true")
    lt = s.add_parser("linha-do-tempo"); lt.add_argument("--dia", default=None); lt.add_argument("--md", action="store_true"); lt.add_argument("--json", action="store_true")
    st = s.add_parser("seller-turbo"); st.add_argument("--json", action="store_true")
    es = s.add_parser("esperar"); es.add_argument("--falha", action="store_true"); es.add_argument("--merge", action="store_true")
    es.add_argument("--feita", type=int, default=None); es.add_argument("--travada", action="store_true"); es.add_argument("--timeout", default="55m")
    lc = s.add_parser("lancar"); lc.add_argument("executor", choices=["codex"]); lc.add_argument("--brief", required=True)
    lc.add_argument("--modelo", default="gpt-6-sol"); lc.add_argument("--dir", required=True); lc.add_argument("--titulo"); lc.add_argument("--projeto", default="")
    lc.add_argument("--forcar", action="store_true")
    bf = s.add_parser("brief").add_subparsers(dest="sub", required=True)
    bn = bf.add_parser("novo"); bn.add_argument("--titulo", required=True); bn.add_argument("--nivel", type=int, default=2); bn.add_argument("--projeto", default=""); bn.add_argument("--saida"); bn.add_argument("--mecanico", action="store_true"); bn.add_argument("--tipo", default="")
    bv = bf.add_parser("validar"); bv.add_argument("arquivo")
    dr = s.add_parser("diario"); dr.add_argument("linha"); dr.add_argument("--arquivo")
    ml = s.add_parser("melhorias").add_subparsers(dest="sub", required=True)
    ma = ml.add_parser("analisar"); mg = ma.add_mutually_exclusive_group(); mg.add_argument("--dia", action="store_true"); mg.add_argument("--semana", action="store_true")
    mlst = ml.add_parser("lista"); mlst.add_argument("--estado", choices=["sugerida", "aplicada", "aprovada", "recusada", "feita"]); mlst.add_argument("--json", action="store_true")
    m1 = s.add_parser("melhoria").add_subparsers(dest="sub", required=True)
    for nome in ("aprovar", "recusar", "feita"):
        x = m1.add_parser(nome); x.add_argument("id", type=int)
    mn = m1.add_parser("nova"); mn.add_argument("--problema", required=True); mn.add_argument("--evidencia", default=""); mn.add_argument("--proposta", default="")
    mn.add_argument("--ganho", default=""); mn.add_argument("--custo", default=""); mn.add_argument("--executor", default="Sonnet"); mn.add_argument("--risco", default="baixo")
    s.add_parser("fila"); dz = s.add_parser("desperdicio"); dz.add_argument("--json", action="store_true")
    pc = s.add_parser("placar"); pc.add_argument("--json", action="store_true")
    ex = s.add_parser("executores"); ex.add_argument("--json", action="store_true"); ex.add_argument("--limite", type=int, default=15)
    st_ = s.add_parser("status"); st_.add_argument("--json", action="store_true"); st_.add_argument("--fixadas", default="", help="ids (local_… ou CLI) das sessões fixadas, separados por vírgula")
    ar_ = s.add_parser("arquivada"); ar_.add_argument("sessao"); ar_.add_argument("--motivo", default="")
    s.add_parser("arquivador-erro", help="anota o erro de uma rodada do arquivador (aparece no cartão Arquivador)").add_argument("linha")
    s.add_parser("ponte").add_argument("--json", action="store_true")
    rd = s.add_parser("resumo-dia"); rd.add_argument("--json", action="store_true")
    es = s.add_parser("esperando"); es.add_argument("--json", action="store_true")
    av = s.add_parser("ao-vivo"); av.add_argument("--json", action="store_true")
    sc = s.add_parser("skills"); sc.add_argument("acao", nargs="?", choices=["custo"]); sc.add_argument("--semana", action="store_true")
    sc.add_argument("--mes", action="store_true"); sc.add_argument("--json", action="store_true")
    cu = s.add_parser("custos"); cg = cu.add_mutually_exclusive_group(); cg.add_argument("--dia", action="store_true"); cg.add_argument("--semana", action="store_true"); cg.add_argument("--mes", action="store_true")
    cu.add_argument("--por", default="modelo", choices=["projeto", "tarefa", "modelo", "agente"]); cu.add_argument("--json", action="store_true")
    cu.add_argument("--csv", action="store_true"); cu.add_argument("--md", action="store_true")
    mt = s.add_parser("metricas"); mt.add_argument("--json", action="store_true")
    sk = s.add_parser("skill"); sk.add_argument("acao", choices=["ativar", "desativar"]); sk.add_argument("nome")

    r = s.add_parser("recomendar"); r.add_argument("descricao"); r.add_argument("--json", action="store_true")
    return p


def main(argv=None, out=None):
    out = out or sys.stdout
    P = lambda *a, **k: print(*a, file=out, **k)
    a = montar().parse_args(argv)
    try:
        if a.cmd == "tarefa":
            if a.sub == "nova":
                P(db.tarefa_nova(a.titulo, a.nivel, a.projeto, a.executor, _ler_brief(a.brief), a.tipo, a.quando_sobrar))
            elif a.sub == "pegar":
                db.tarefa_pegar(a.id, a.agente or os.environ.get("ORQ_AGENTE", ""), a.forcar)
                P("tarefa %d pega" % a.id)
            elif a.sub == "etapa":
                db.tarefa_etapa(a.id, a.texto); P("etapa da tarefa %d: %s" % (a.id, a.texto))
            elif a.sub == "pausar":
                db.tarefa_pausar(a.id, a.motivo); P("tarefa %d pausada" % a.id)
            elif a.sub == "retomar":
                db.tarefa_retomar(a.id); P("tarefa %d retomada" % a.id)
            elif a.sub == "feita":
                est = db.tarefa_feita(a.id, a.resultado, a.agente, a.tokens_in, a.tokens_out, a.modelo)
                P("tarefa %d %s" % (a.id, "aguardando revisão (orq tarefa revisada %d)" % a.id if est == "aguardando" else "feita"))
                from . import custo
                P(custo.resumo(db.tarefa(a.id))["texto"])
            elif a.sub == "revisada":
                db.tarefa_revisada(a.id, a.por); P("tarefa %d revisada → feita" % a.id)
            elif a.sub == "voltou":
                db.tarefa_voltou(a.id, a.motivo); P("tarefa %d voltou ao executor" % a.id)
            elif a.sub == "gate":
                db.tarefa_gate(a.id, a.falhou); P("gate da tarefa %d: %s" % (a.id, "FALHOU" if a.falhou else "passou"))
            elif a.sub == "bloqueada":
                db.tarefa_bloqueada(a.id, a.motivo, a.agente); P("tarefa %d bloqueada" % a.id)
            else:
                ts = db.tarefas(a.estado, a.projeto)
                if a.json:
                    P(json.dumps(ts, ensure_ascii=False, indent=1))
                else:
                    for x in ts:
                        P("#%-4d %-9s N%d %-14s %-12s %s" % (x["id"], x["estado"], x["nivel"], x["agente"] or "—", x["projeto"] or "—", x["titulo"]))
        elif a.cmd == "msg":
            P("mensagem %d enviada" % db.msg(a.de or _eu(), a.para, a.texto))
        elif a.cmd == "msgs":
            for x in reversed(db.msgs(a.agente, nao_lidas=not a.todas, marcar=bool(a.agente and not a.todas))):
                P("[%s] %s → %s: %s" % (_quando(x["criado"]), x["de"], x["para"], x["texto"]))
        elif a.cmd == "cota":
            if a.json:
                from . import cotas as cotas_mod
                ags = cotas_mod.informar(_ler_json(a.json)); P("cotas informadas: %s" % ", ".join(ags))
            else:
                if not a.agente or a.usado is None:
                    raise ValueError("use: orq cota <agente> --usado N [--renova DATA] [--janela-h H], ou --json ARQUIVO")
                db.cota_registrar(a.agente, a.usado, a.renova, "informado", a.janela_h); P("cota de %s: %.0f%% usado" % (a.agente, a.usado))
        elif a.cmd == "cotas-informar":
            from . import cotas as cotas_mod
            ags = cotas_mod.informar(_ler_json(a.arquivo)); P("cotas informadas: %s" % ", ".join(ags))
        elif a.cmd == "cotas":
            from . import cotas as cotas_mod
            cs = db.cotas()
            if not cs:
                P("sem cotas registradas (as IAs informam com `orq cota` ou `orq cotas-informar`)")
            for c in cs:
                P("%-34s usado %5.1f%%  disponível %5.1f%%  renova %s (em %s h)  ritmo %s %s (permitido %s)  previsão %s%%%s%s" % (
                    cotas_mod.nome(c["agente"]), c["usado_pct"], c["disponivel_pct"], _quando(c["renova_em"]), c["horas_ate_renovar"],
                    c["ritmo_atual"], c["ritmo_unidade"], c["ritmo_permitido"], c["previsao_fim_pct"],
                    "  ⚠ VAI ESTOURAR" if c["estoura"] else "",
                    "  [%sconfirmado %s%% há %s%s]" % ("estimado; " if c["origem"] == "estimado" else "zerou na renovação; " if c["resetou"] else "", c["confirmado_pct"],
                                                  cotas_mod.idade_texto(c["idade_leitura_s"]), ", leitura velha" if c["leitura_velha"] else "")))
        elif a.cmd == "pulso":
            db.pulso(a.agente or _eu(), a.nota); P("pulso de %s" % (a.agente or _eu()))
        elif a.cmd == "projeto":
            if a.sub == "novo":
                db.projeto_novo(a.nome, a.descricao); P("projeto %s" % a.nome)
            else:
                for x in db.projetos():
                    P("%-24s %s  %s" % (x["nome"], x["tarefas"] or "", x["descricao"]))
        elif a.cmd == "processo":
            from . import acoes
            r = {"pausar": acoes.pausar, "retomar": acoes.retomar}.get(a.acao, lambda i: acoes.encerrar(i, a.sim))(a.pid)
            P("%s: pid %d, %d processo(s)" % (r["acao"], r["pid"], r["processos"]))
        elif a.cmd == "linha-do-tempo":
            from . import timeline
            P(json.dumps(timeline.dia(a.dia), ensure_ascii=False, indent=1) if a.json else timeline.markdown(a.dia))
        elif a.cmd == "seller-turbo":
            from . import sellerturbo
            d = sellerturbo.atualizar()
            if a.json:
                P(json.dumps(d, ensure_ascii=False, indent=1))
            else:
                P("erro: %s" % d["erro"] if d["erro"] else "PRs abertos (qa/ fix/ jarvis/ t4xx): %d" % len(d["prs"]))
                for p in d["prs"]:
                    P("#%-4d %-10s %-32s %s" % (p["numero"], p["gate"].upper(), p["ramo"][:32], p["titulo"][:60]))
                for r in d["deploys"][:5]:
                    P("deploy %-10s %-14s %s" % (r["resultado"], r["workflow"][:14], r["titulo"][:60]))
        elif a.cmd == "esperar":
            from . import esperar
            cod, linha = esperar.esperar(a.falha, a.merge, a.feita, a.travada, esperar.parse_timeout(a.timeout))
            P(linha)
            return cod
        elif a.cmd == "lancar":
            from . import lancar
            r = lancar.lancar(a.brief, a.modelo, a.dir, a.titulo, a.projeto, a.forcar)
            P("tarefa %d · codex %s · pid %d · log %s" % (r["tarefa"], r["modelo"], r["pid"], r["log"]))
        elif a.cmd == "brief":
            from . import brief
            if a.sub == "novo":
                txt = brief.novo(a.titulo, a.nivel, a.projeto, a.mecanico, a.tipo)
                if a.saida:
                    with open(os.path.expanduser(a.saida), "w", encoding="utf-8") as f:
                        f.write(txt)
                    P("brief criado em %s" % a.saida)
                else:
                    P(txt)
            else:
                with open(a.arquivo, encoding="utf-8") as f:
                    v = brief.validar(f.read())
                P("OK: brief completo" if v["ok"] else "INCOMPLETO: seções ausentes %s; %d campo(s) [PREENCHER]" % (v["secoes_ausentes"] or "nenhuma", v["por_preencher"]))
                return 0 if v["ok"] else 1
        elif a.cmd == "diario":
            from . import brief
            P("diário: %s" % brief.diario(a.linha, a.arquivo))
        elif a.cmd == "melhorias":
            from . import melhorias
            if a.sub == "analisar":
                novas = melhorias.analisar("semana" if a.semana else "dia")
                P("%d sugestão(ões) nova(s)" % len(novas))
                for m in novas:
                    P("#%-3d [%.0f] %s\n   ↳ %s" % (m["id"], m["prioridade"], m["problema"], m["proposta"]))
            else:
                ls = melhorias.lista(a.estado)
                if a.json:
                    P(json.dumps(ls, ensure_ascii=False, indent=1))
                for m in ([] if a.json else ls):
                    P("#%-3d %-9s [%.0f] %s  (%s · %s)" % (m["id"], m["estado"], m["prioridade"], m["problema"], m["executor"], m["risco"]))
        elif a.cmd == "melhoria":
            from . import melhorias
            if a.sub == "aprovar":
                P("melhoria %d aprovada → tarefa %d" % (a.id, melhorias.aprovar(a.id)))
            elif a.sub == "recusar":
                melhorias.recusar(a.id); P("melhoria %d recusada" % a.id)
            elif a.sub == "feita":
                melhorias.feita(a.id); P("melhoria %d feita" % a.id)
            else:
                i = melhorias.registrar(melhorias._s("manual:%s" % a.problema[:60], a.problema, a.evidencia, a.proposta, a.ganho, a.custo, a.executor, a.risco, 5.0), "manual")
                P("melhoria %s" % (i if i else "já existe (mesma chave)"))
        elif a.cmd == "fila":
            from . import orcamento
            fl = orcamento.fila()
            for t in fl:
                P("#%-4d EM ESPERA  %s\n   ↳ %s" % (t["id"], t["titulo"], t["espera"]))
            if not fl:
                P("nenhuma tarefa em espera por orçamento")
        elif a.cmd == "desperdicio":
            from . import desperdicio
            ach = desperdicio.analisar(__import__("orquestra.coletores", fromlist=["x"]).claude_sessoes())
            if a.json:
                P(json.dumps(ach, ensure_ascii=False, indent=1))
            for x in ([] if a.json else ach):
                P("%s (%s): %s" % (x["projeto"], x["modelo"], x["sugestao"]))
            if not ach and not a.json:
                P("sem desperdício detectado (limite: >15 leituras/testes em 10 min no nível 3)")
        elif a.cmd == "placar":
            rs = db.placar()
            if a.json:
                P(json.dumps(rs, ensure_ascii=False, indent=1))
            for r in ([] if a.json else rs):
                P("N%d %-18s %-9s total %-3d primeira %-3d voltou %-3d quebrou %-3d (%s de primeira)" % (
                    r["nivel"], r["modelo"] or "?", r["tipo"] or "—", r["total"], r["primeira"] or 0, r["voltou"] or 0, r["quebrou"] or 0,
                    "%.0f%%" % (r["taxa_primeira"] * 100)))
            if not rs and not a.json:
                P("placar vazio: nenhuma tarefa revisada ainda")
            if not a.json:
                import time as _t
                for r in db.placar_executores(_t.time() - 7 * 86400):
                    P("executores (7 d) %-22s %-28s %3d tarefas · %d feitas · %d bloqueadas · %d rodando · ≈ R$ %.2f" % (
                        r["modelo"] or "?", r["executor"] or "—", r["total"], r["feitas"] or 0, r["bloqueadas"] or 0, r["rodando"] or 0, r["brl"] or 0))
        elif a.cmd == "status":
            import time as _t
            from . import trabalhos
            t0 = _t.perf_counter()
            r = trabalhos.status(fixadas=set(x for x in a.fixadas.split(",") if x))
            trabalhos.registrar_tempo_status(_t.perf_counter() - t0)       # meta < 1 s; aparece no cartão Arquivador
            if a.json:
                P(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                P("  ".join("%s %d" % (k.replace("_", " "), v) for k, v in r["contagem"].items() if v))
                for t in r["trabalhos"]:
                    P("%-15s %-6s %s%s" % (t["estado"].upper(), ("#%d" % t["pr"]["numero"]) if t["pr"] else "—", t["titulo"][:70], "  [arquivar: %s]" % t["arquivar_motivo"] if t["arquivar"] else ""))
        elif a.cmd == "arquivada":
            from . import trabalhos
            trabalhos.registrar_arquivada(a.sessao, a.motivo)
            P("registrado")
        elif a.cmd == "arquivador-erro":
            from . import trabalhos
            trabalhos.registrar_erro_arquivador(a.linha)
            P("erro anotado")
        elif a.cmd == "ponte":
            from . import ponte
            r = ponte.sincronizar()
            if r is None:
                return 0                      # sem token no Keychain: não faz nada e sai calado
            if a.json:
                P(json.dumps(r, ensure_ascii=False))
            elif r["erro"]:
                print("orq ponte: %s" % r["erro"], file=sys.stderr)
                return 1
            else:
                P("ponte: foto enviada, %d comando(s) aplicado(s)" % r["comandos"])
        elif a.cmd == "executores":
            from . import executores
            r = executores.sincronizar()
            ts = [t for t in db.tarefas() if t.get("auto")][:a.limite]
            if a.json:
                P(json.dumps({"sincronizacao": r, "tarefas": ts}, ensure_ascii=False, indent=1))
            else:
                P("sincronizado: %(novas)d nova(s), %(atualizadas)d atualizada(s) (%(subagentes)d subagentes, %(codex)d rollouts do Codex)" % r)
                for t in ts:
                    P("#%-4d %-10s %-18s %-9s %s\n      ↳ mãe %s · %s tok · ≈ R$ %s · %s" % (
                        t["id"], t["estado"], (t["modelo"] or "?")[:18], datetime.fromtimestamp(t["iniciado"]).strftime("%d/%m %H:%M") if t["iniciado"] else "—",
                        t["titulo"][:70], (t["sessao_mae"] or "—")[:8], t["tokens_in"] + t["tokens_out"] + t["tokens_cache"], "%.2f" % t["custo_brl"] if t["custo_brl"] is not None else "—",
                        (t["resultado"] or "").splitlines()[0][:80] if t["resultado"] else "sem resultado"))
        elif a.cmd in ("resumo-dia", "esperando"):
            from . import painel
            itens = _ao_vivo()["itens"]
            r = painel.resumo_dia(itens) if a.cmd == "resumo-dia" else painel.esperando(itens)
            if a.json:
                P(json.dumps(r, ensure_ascii=False, indent=1))
            elif a.cmd == "resumo-dia":
                for l in r["linhas"]:
                    P(l)
            else:
                for w in r:
                    P("%s — parada há %s\n   “%s”\n   %s" % (w["titulo"], _dur(w["parada_s"]), w["frase"][:150], w["link"] or "(o app não conhece o id desta sessão)"))
                if not r:
                    P("ninguém esperando você")
        elif a.cmd == "ao-vivo":
            snap = _ao_vivo()
            if a.json:
                P(json.dumps(snap, ensure_ascii=False, indent=1))
            else:
                r = snap["resumo"]
                P("ativas %d · travadas %d · esperando dono %d · pausadas %d · falhas %d  (energia: %s)" % (
                    r["ativa"], r["travada"], r["esperando_dono"], r["pausada"], r["falhou"], snap.get("energia", "?")))
                for i in snap["itens"]:
                    P("%-16s %-15s N%d cpu %-5s %-9s %s\n   ↳ %s | etapa: %s" % (
                        i["id"], i["estado"].upper(), i["nivel"], i["cpu"] if i["cpu"] is not None else "—",
                        _dur(i["tempo_s"]), (i["titulo"] or i["nome"])[:70], i["motivo"], (i["etapa_agente"] or i["etapa"] or "—")[:90]))
        elif a.cmd == "custos":
            from . import custos, transcritos
            transcritos.atualizar()
            rel = custos.relatorio("mes" if a.mes else "semana" if a.semana else "dia", a.por)
            if a.json:
                P(json.dumps(rel, ensure_ascii=False, indent=1))
            elif a.csv:
                P(custos.csv_texto(rel), end="")
            elif a.md:
                P(custos.markdown(rel), end="")
            else:
                P("Custos (%s) — ESTIMATIVA, equivalente a preço de API: R$ %.2f  · Claude %.2f · Codex %.2f (%.1f%% do ciclo atual; %.1f pts no período) · máquina %.2f · CI %.2f" % (
                    rel["periodo"], rel["total_brl"], rel["partes"]["claude"], rel["partes"]["codex"], rel["codex"]["cota_pct"], rel["codex"]["pontos_periodo"], rel["partes"]["maquina"], rel["partes"]["ci"]))
                P("economia vs. tudo no modelo do orquestrador: R$ %.2f" % rel["economia_vs_orquestrador"])
                for g in rel["agrupado"][:12]:
                    P("  %-44s R$ %9.2f   in %-10d out %-9d cache-leit %-12d cache-esc %d" % (g["chave"][:44], g["brl"], g["tin"], g["tout"], g["cache_leitura"], g["cache_escrita"]))
                for al in rel["alertas"]:
                    P("⚠ " + al)
        elif a.cmd == "metricas":
            from . import metricas
            m = metricas.resumo()
            if a.json:
                P(json.dumps(m, ensure_ascii=False, indent=1))
            for c in ([] if a.json else m["cartoes"]):
                P("%-46s %-10s atual %-8s meta %-18s %s" % (c["titulo"], c["estado"].upper(), c["atual"], c["meta"], c["serie"]))
        elif a.cmd == "skills" and a.acao == "custo":
            from . import skillcusto, transcritos
            transcritos.atualizar()
            r = skillcusto.relatorio("mes" if a.mes else "semana")
            if a.json:
                P(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                P("Custo por skill (%s) — ESTIMATIVA · total Claude R$ %.2f" % (r["periodo"], r["total_claude_brl"]))
                for x in r["skills"][:20]:
                    P("%-34s usos %-3d carregar R$ %-7.2f guiado R$ %-8.2f (%.1f%% do gasto) R$/resultado %-8s → %s" % (
                        x["skill"][:34], x["usos"], x["brl_carregar"], x["brl_guiado"], x["pct_gasto"], x["custo_por_resultado"] if x["custo_por_resultado"] is not None else "—", x["acao"].upper()))
        elif a.cmd == "skills":
            for x in skills.listar():
                P("%-34s %s" % (x["nome"], "ativa" if x["ativa"] else "DESATIVADA"))
        elif a.cmd == "skill":
            (skills.ativar if a.acao == "ativar" else skills.desativar)(a.nome)
            P("skill %s %s" % (a.nome, "ativada" if a.acao == "ativar" else "desativada (link movido, nada apagado)"))
        elif a.cmd == "recomendar":
            rec = cerebro.recomendar(a.descricao, db.cotas(), db.placar())
            if a.json:
                P(json.dumps(rec, ensure_ascii=False, indent=1))
            else:
                P("Nível %d · %s (%s)%s" % (rec["nivel"], rec["executor"], rec["modelo"], " · precisa do dono" if rec["dono"] else ""))
                P(rec["porque"])
    except (KeyError, ValueError, PermissionError) as e:
        print("orq: %s" % (e.args[0] if e.args else e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
