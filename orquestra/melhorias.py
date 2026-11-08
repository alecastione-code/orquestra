"""Analisador de melhorias (skill auto-melhoria): lê o que aconteceu e grava sugestões com evidência. Só sugere."""
import glob
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta

from . import aovivo, coletores, config, custo, db, skills

PERGUNTA_ESTADO = re.compile(r"\b(status|estado|como (est[aá]|anda)|j[aá] terminou|cad[eê]|andamento|em que p[eé]|deu certo)\b", re.I)
TRIVIAL = re.compile(r"^(ls|cd|pwd|cat|echo|git (status|log|diff|add|commit)|head|tail|sleep|true|which|open)\b")
NIVEL_DO_EXECUTOR = {"haiku": 1, "sonnet": 2, "orquestrador": 3, "script": 0, "codex": 2}


def _janela(periodo):
    dias = 7 if periodo == "semana" else 1
    return time.time() - dias * 86400, dias


def _s(chave, problema, evidencia, proposta, ganho, custo_, executor, risco, prioridade):
    return {"chave": chave, "problema": problema, "evidencia": evidencia, "proposta": proposta, "ganho": ganho,
            "custo": custo_, "executor": executor, "risco": risco, "prioridade": prioridade}


def _norm(txt):
    return re.sub(r"\d+", "N", " ".join(txt.lower().split()))[:80]


# ------------------------------------------------------------------ heurísticas
def h_trabalho_repetido(ini, dias):
    out = []
    cont = Counter(_norm(t["titulo"]) for t in db.tarefas() if t["criado"] >= ini and t["nivel"] <= 2 and not t.get("auto"))
    for titulo, n in cont.items():
        if n >= 3:
            out.append(_s("repetido-tarefa:" + titulo, "Mesma tarefa criada %d× à mão" % n, "título normalizado: “%s” (%d vezes no período)" % (titulo, n),
                          "Virar script, comando `orq`, sonda ou função de app (skill analise-vira-funcao).", "%d× menos retrabalho por período" % n,
                          "baixo (script pequeno)", "Sonnet", "baixo", n * 2.0))
    cmds = Counter()
    for s in coletores.claude_sessoes(horas=24 * dias):
        for c in _bash_do_transcript(s.get("arquivo")):
            cmds[c] += 1
    for c, n in cmds.most_common(5):
        if n >= 3:
            out.append(_s("repetido-cmd:" + c[:60], "Comando rodado %d× nas sessões" % n, "`%s` (redigido), %d vezes" % (c, n),
                          "Criar um script/atalho (ou comando `orq`) para esse passo e usá-lo nos briefs.", "menos tokens e erro de digitação",
                          "baixo", "Haiku", "baixo", n * 1.5))
    return out


def _bash_do_transcript(arq):
    if not arq:
        return []
    saida = []
    for l in coletores._cauda(arq, 524288).split("\n"):
        if '"Bash"' not in l or '"command"' not in l:
            continue
        try:
            o = json.loads(l)
            for x in o["message"]["content"]:
                if x.get("type") == "tool_use" and x.get("name") == "Bash":
                    c = aovivo.ocultar((x.get("input") or {}).get("command", ""), 90)
                    if len(c) >= 25 and not TRIVIAL.match(c):
                        saida.append(c)
        except (ValueError, KeyError, TypeError):
            continue
    return saida


def h_espera_sem_vigia(ini, dias):
    ms = [m for m in db.msgs(limite=500) if m["de"] == "dono" and m["criado"] >= ini and PERGUNTA_ESTADO.search(m["texto"])]
    if not ms:
        return []
    ex = " | ".join(aovivo.ocultar(m["texto"], 50) for m in ms[:3])
    return [_s("espera-sem-vigia", "O dono precisou perguntar o estado de tarefas (%d vez(es))" % len(ms), "mensagens do dono: %s" % ex,
               "Usar `orq esperar --feita/--falha/--travada` em segundo plano e o hook de pulso; avisar sozinho no fim do trabalho.",
               "o dono deixa de ter que perguntar", "baixo (comandos já existem)", "orquestrador", "baixo", 3.0 + len(ms))]


def h_barata_voltou(ini, dias):
    out = []
    for t in db.tarefas():
        if not t.get("auto") and t["nivel"] in (1, 2) and t["voltas"] >= 2 and t["atualizado"] >= ini:
            out.append(_s("voltou2:%d" % t["id"], "Tarefa de nível %d voltou %d× da revisão" % (t["nivel"], t["voltas"]),
                          "#%d %s (tipo %s, modelo %s)" % (t["id"], t["titulo"][:70], t["tipo"] or "?", t["modelo"] or "?"),
                          "Reescrever o brief (critério de pronto e testes) ou subir um nível para esse tipo; conferir o placar.",
                          "menos idas e vindas, menos tokens", "baixo", "orquestrador", "baixo", 6.0))
    return out


def h_cara_bracal(ini, dias):
    out = []
    bracais = [t for t in db.tarefas() if t["nivel"] == 3 and t["tipo"] in ("leitura", "script") and t["criado"] >= ini]
    if bracais:
        out.append(_s("caro-bracal", "%d tarefa(s) braçais feitas no nível 3" % len(bracais), "; ".join("#%d %s" % (t["id"], t["titulo"][:50]) for t in bracais[:4]),
                      "Descer essas tarefas para Haiku/script e revisar a regra do cérebro para esses termos.", "cota do orquestrador", "baixo", "orquestrador", "baixo", 4.0 + len(bracais)))
    desp = [e for e in db.eventos(500) if e["tipo"] == "desperdicio" and e["quando"] >= ini]
    if desp:
        out.append(_s("desperdicio-recorrente", "Detector de desperdício disparou %d× no período" % len(desp), desp[0]["texto"][:160],
                      "Mandar a varredura/leitura para Haiku desde o início; checar `orq desperdicio` antes de abrir sessão cara.", "economia no modelo caro",
                      "baixo", "orquestrador", "baixo", 5.0 + len(desp)))
    return out


def h_skill_sem_uso(ini, dias):
    usadas = set()
    for p in glob.glob(os.path.join(config.claude_home(), "projects", "*", "*.jsonl")):
        try:
            if os.path.getmtime(p) < time.time() - 7 * 86400 or os.path.basename(p) in config.ARQUIVOS_PROIBIDOS:
                continue
            with coletores._abrir(p, encoding="utf-8", errors="replace") as f:
                for l in f:
                    if '"name":"Skill"' in l or '"name": "Skill"' in l:
                        usadas.update(re.findall(r'"skill"\s*:\s*"([^"]+)"', l))
        except OSError:
            continue
    ativas = [s["nome"] for s in skills.listar() if s["ativa"]]
    sem = sorted(n for n in ativas if n not in usadas and n.split(":")[-1] not in usadas)
    if len(sem) < 3 or not usadas:
        return []
    return [_s("skills-sem-uso", "%d de %d skills ativas não foram usadas em 7 dias" % (len(sem), len(ativas)), "sem uso: " + ", ".join(sem[:15]) + ("…" if len(sem) > 15 else ""),
               "Desativar (reversível: `orq skill desativar`) ou reescrever a descrição das que nunca disparam; isso reduz o ruído de seleção.",
               "menos contexto carregado por sessão", "baixo (nada é apagado)", "orquestrador", "alto", 3.0)]


def h_cota_fora_do_ritmo(ini, dias):
    out = []
    for c in db.cotas():
        if c["acima_ritmo"]:
            out.append(_s("cota-ritmo:%s:%s" % (c["agente"], datetime.now().strftime("%Y-%m-%d")), "Cota do %s fora do ritmo do dia" % c["agente"],
                          "%s (sobra %.0f%%, renova em %.1f dias)" % (c["texto_ritmo"], c["restante"], c["dias_ate_renovar"] or 0),
                          "Adiar o não urgente com `--quando-sobrar` e mandar leitura para Haiku/script; ver o que consumiu em `orq linha-do-tempo`.",
                          "evita zerar a cota antes da renovação", "baixo", "orquestrador", "baixo", 8.0))
    return out


def h_travamentos_e_gates(ini, dias):
    out, ev = [], [e for e in db.eventos(1000) if e["quando"] >= ini]
    trav = [e for e in ev if e["tipo"] == "alerta" and "TRAVADA" in e["texto"]]
    gate = [e for e in ev if e["tipo"] == "alerta" and "GATE FALHOU" in e["texto"]]
    if trav:
        out.append(_s("travamentos", "%d travamento(s) de agente no período" % len(trav), trav[0]["texto"][:160],
                      "Pôr `orq etapa`/pulso no brief e `--timeout` nas tarefas longas; olhar onde trava (log) antes de relançar.", "menos horas paradas", "baixo", "orquestrador", "baixo", 4.0 + len(trav)))
    por_motivo = Counter(e["texto"].split("): ", 1)[-1] for e in gate)  # só o(s) check(s) que falharam
    for motivo, n in por_motivo.items():
        if n >= 2:
            out.append(_s("gate:" + motivo[:60], "Gate falhou %d× pelo mesmo motivo" % n, motivo[:160],
                          "Criar sonda/teste que pegue isso antes do PR (regra: mesmo erro 2× vira automação).", "menos PR quebrado", "médio", "Sonnet", "baixo", 7.0 + n))
    return out


def h_correcoes_do_dono(ini, dias):
    out = []
    for p in glob.glob(os.path.join(config.claude_home(), "projects", "*", "memory", "*.md")):
        try:
            if os.path.getmtime(p) < ini or os.path.basename(p) == "MEMORY.md":
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read(4000)
        except OSError:
            continue
        fm = skills.frontmatter(txt)
        if "feedback" not in (fm.get("type", "") + txt[:400].lower()):
            continue
        nome = fm.get("name") or os.path.basename(p)
        out.append(_s("feedback:" + nome, "Correção recente do dono: %s" % aovivo.ocultar(fm.get("description", nome), 100),
                      "memória feedback `%s` atualizada" % nome, "Conferir se existe regra/sonda/brief que impeça a repetição; se não, criar (e citar a memória).",
                      "o dono não precisa corrigir de novo", "baixo", "orquestrador", "baixo", 5.0))
    return out


def h_skills_custo(ini, dias):
    from . import skillcusto
    out = []
    for r in skillcusto.relatorio("mes")["skills"]:
        if r["acao"] == "manter" or r["usos"] < 2:
            continue
        if r["acao"] == "pausar":  # desligar skill é sempre decisão do dono
            out.append(_s("skill-pausar:" + r["skill"], "Skill %s cara demais para o resultado" % r["skill"],
                          "R$ %.2f no mês (%.1f%% do gasto), %d resultados; %s" % (r["brl_total"], r["pct_gasto"], r["resultados"], r["porque"]),
                          "Pausar a skill (reversível: `orq skill desativar %s`) e reavaliar em 2 semanas." % r["skill"], "R$ %.2f/mês" % r["brl_total"],
                          "baixo", "orquestrador", "alto", 6.0 + r["pct_gasto"]))
        else:
            out.append(_s("skill-otimizar:" + r["skill"], "Skill %s pode ficar mais barata" % r["skill"],
                          "R$ %.2f no mês, %d usos; %s" % (r["brl_total"], r["usos"], r["porque"]), skillcusto.proposta_otimizar(r),
                          "menos tokens por uso", "baixo (edição de texto)", "Sonnet", "baixo", 3.0 + r["pct_gasto"]))
    return out


def h_metricas(ini, dias):
    from . import metricas
    out = []
    cs, extra = metricas.cartoes()
    for c in cs:
        if c["estado"] != "ruim":
            continue
        out.append(_s("metrica:" + c["id"], "Métrica fora da meta: %s" % c["titulo"],
                      "meta %s; últimas semanas %s %s (%s)" % (c["meta"], c["serie"], c["unidade"], extra["semanas"]),
                      "Aplicar a ação da skill: %s." % c["dica"], "voltar à meta", "baixo", "orquestrador", "baixo", 4.0))
    return out


HEURISTICAS = [h_skills_custo, h_metricas, h_trabalho_repetido, h_espera_sem_vigia, h_barata_voltou, h_cara_bracal, h_skill_sem_uso,
               h_cota_fora_do_ritmo, h_travamentos_e_gates, h_correcoes_do_dono]


# ------------------------------------------------------------------ persistência
SENSIVEL = re.compile(r"\b(pre[cç]o|ads|an[uú]ncio pago|dinheiro|reembolso|margem|produ[cç][aã]o|permiss|segredo|token|credencial|senha|apag|delet|exclu|remov|desativ|pausar|jeito do dono|fluxo do dono|como o dono)", re.I)


def classificar_risco(s):
    """Regra de decisão: 'alto' se mexe em dinheiro/preço/Ads/produção/permissões/segredos, apaga algo, desliga skill
    ou muda o jeito do dono trabalhar; senão 'baixo' (óbvia, reversível). Marcação explícita 'alto' vence."""
    if (s.get("risco") or "").lower().startswith("alto"):
        return "alto"
    txt = " ".join(str(s.get(k, "")) for k in ("problema", "proposta", "custo"))
    return "alto" if SENSIVEL.search(txt) else "baixo"


def decidir(s):
    """'aplicada' (executa/delega direto) para risco baixo; 'sugerida' (o dono aprova) para risco alto."""
    return "sugerida" if classificar_risco(s) == "alto" else "aplicada"


def registrar(s, origem="analise"):
    """Grava sugestão (dedupe pela chave enquanto não foi recusada/feita). Devolve id ou None."""
    t = db.agora()
    risco = classificar_risco(s)
    with db.conectar() as con:
        if con.execute("SELECT 1 FROM melhorias WHERE chave=?", (s["chave"],)).fetchone():
            return None
        cur = con.execute("INSERT INTO melhorias(chave,problema,evidencia,proposta,ganho,custo,executor,risco,prioridade,origem,criado,atualizado) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (s["chave"], s["problema"], s["evidencia"], s["proposta"], s["ganho"], s["custo"],
                                                              s["executor"], risco, s["prioridade"], origem, t, t))
        mid = cur.lastrowid
        db.evento(con, "melhoria", "sugerida #%d: %s" % (mid, s["problema"]))
    if decidir(dict(s, risco=risco)) == "aplicada":
        _aplicar(mid)
    return mid


def _aplicar(mid):
    """Baixo risco e reversível: cria a tarefa já no quadro, avisa em uma linha no painel e ao orquestrador."""
    m = _get(mid)
    tid = _criar_tarefa(m)
    with db.conectar() as con:
        con.execute("UPDATE melhorias SET estado='aplicada', tarefa_id=?, atualizado=? WHERE id=?", (tid, db.agora(), mid))
        db.evento(con, "melhoria-aplicada", "melhoria #%d aplicada (risco baixo): %s → tarefa #%d" % (mid, m["problema"][:100], tid))
    db.msg("orquestra", "orquestrador", "Melhoria #%d aplicada sozinha (risco baixo): %s → tarefa #%d. Recuse no painel se discordar." % (mid, m["problema"][:90], tid))


def analisar(periodo="dia"):
    ini, dias = _janela(periodo)
    novas = []
    for h in HEURISTICAS:
        try:
            for s in h(ini, dias):
                i = registrar(s)
                if i:
                    novas.append(dict(s, id=i))
        except Exception as e:  # uma heurística quebrada não derruba as outras
            with db.conectar() as con:
                db.evento(con, "melhoria", "heurística %s falhou: %s" % (h.__name__, e))
    with db.conectar() as con:
        db.evento(con, "melhorias-analise", "%s: %d sugestão(ões) nova(s)" % (periodo, len(novas)))
    return sorted(novas, key=lambda x: -x["prioridade"])


def lista(estado=None):
    q, a = "SELECT * FROM melhorias", []
    if estado:
        q += " WHERE estado=?"; a.append(estado)
    with db.conectar() as con:
        return [dict(r) for r in con.execute(q + " ORDER BY CASE estado WHEN 'sugerida' THEN 0 WHEN 'aplicada' THEN 1 WHEN 'aprovada' THEN 1 ELSE 2 END, prioridade DESC, id DESC", a)]


def _get(mid):
    with db.conectar() as con:
        r = con.execute("SELECT * FROM melhorias WHERE id=?", (int(mid),)).fetchone()
    if not r:
        raise KeyError("melhoria %s não existe" % mid)
    return dict(r)


def _criar_tarefa(m):
    nivel = next((v for k, v in NIVEL_DO_EXECUTOR.items() if k in (m["executor"] or "").lower()), 2)
    brief = "# Melhoria #%d\n\n## Problema\n%s\n\n## Evidência\n%s\n\n## Proposta\n%s\n\n## Ganho esperado\n%s\n\n## Custo / risco\n%s / %s\n" % (
        m["id"], m["problema"], m["evidencia"], m["proposta"], m["ganho"], m["custo"], m["risco"])
    return db.tarefa_nova("Melhoria #%d: %s" % (m["id"], m["problema"][:90]), nivel, "orquestra", m["executor"], brief, "melhoria")


def aprovar(mid):
    m = _get(mid)
    if m["estado"] != "sugerida":
        raise ValueError("melhoria %s já está %s" % (mid, m["estado"]))
    tid = _criar_tarefa(m)
    with db.conectar() as con:
        con.execute("UPDATE melhorias SET estado='aprovada', tarefa_id=?, atualizado=? WHERE id=?", (tid, db.agora(), m["id"]))
        db.evento(con, "melhoria", "aprovada #%d → tarefa #%d" % (m["id"], tid))
    return tid


def recusar(mid, motivo=""):
    m = _get(mid)
    if m["estado"] not in ("sugerida", "aprovada", "aplicada"):
        raise ValueError("melhoria %s já está %s" % (mid, m["estado"]))
    with db.conectar() as con:
        con.execute("UPDATE melhorias SET estado='recusada', atualizado=? WHERE id=?", (db.agora(), m["id"]))
        db.evento(con, "melhoria", "recusada #%d %s" % (m["id"], motivo))


def feita(mid):
    _get(mid)
    with db.conectar() as con:
        con.execute("UPDATE melhorias SET estado='feita', atualizado=? WHERE id=?", (db.agora(), int(mid)))
        db.evento(con, "melhoria", "feita #%s" % mid)


# ------------------------------------------------------------------ rodada diária às 22:00
HORA_RODADA = 22


def deve_rodar(agora=None):
    """True se já passou das 22:00 de hoje e a análise do dia ainda não rodou."""
    agora = agora or datetime.now()
    if agora.hour < HORA_RODADA:
        return False
    inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with db.conectar() as con:
        r = con.execute("SELECT 1 FROM eventos WHERE tipo='melhorias-analise' AND quando>=? AND texto LIKE 'dia:%'", (inicio,)).fetchone()
    return r is None


def rodada_diaria(agora=None):
    if not deve_rodar(agora):
        return None
    novas = analisar("dia")
    if novas:
        try:
            from . import notificar
            notificar.enviar("Orquestra · melhorias", "%d sugestão(ões) nova(s) para você aprovar (aba Melhorias)." % len(novas))
        except Exception:
            pass
    return novas
