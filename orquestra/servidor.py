"""Servidor local do Orquestra: API JSON + painel. Só escuta em 127.0.0.1."""
import json
import mimetypes
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, acoes, autopiloto, cotas as cotas_mod, custos, metricas, skillcusto, transcritos, melhorias, sellerturbo, timeline, vigia, aovivo, cerebro, custo, desperdicio, executores, painel, trabalhos, orcamento, coletores, config, db, skills

INICIO = time.time()
VIVO_CLAUDE = 300     # sessão Claude com atividade nos últimos 5 min = viva
VIVO_PULSO = 900


def agentes(ret=None):
    ret = ret or coletores.retrato()
    out = []
    for s in ret.get("claude_sessoes", []):
        orq = s["nivel"] == 3
        out.append({"id": "claude:" + s["id"], "tipo": "orquestrador" if orq else "claude", "nome": s["titulo"], "detalhe": s["projeto"] + (" · " + s["branch"] if s.get("branch") else ""),
                    "modelo": s["modelo"], "nivel": s["nivel"], "tarefa": "", "ha_segundos": s["ha_segundos"],
                    "vivo": s["ha_segundos"] < VIVO_CLAUDE})
    for p in ret.get("codex_processos", []):
        out.append({"id": "codex:%d" % p["pid"], "tipo": "codex", "nome": p["pasta"], "modelo": p["modelo"],
                    "nivel": p["nivel"], "tarefa": ((p["tarefa"] + " — ") if p["tarefa"] else "") + p["titulo"],
                    "ha_segundos": p["ha_segundos"], "idade_log": p["idade_log"], "vivo": True})
    vivas_pastas = {p["pasta"] for p in ret.get("codex_processos", [])}
    for c in ret.get("codex_sessoes", []):
        if c["ha_segundos"] < VIVO_CLAUDE and c["projeto"] in vivas_pastas:
            continue   # a mesma execução já aparece pelo processo vivo (com o título do brief)
        out.append({"id": "codex-sessao:" + c["id"], "tipo": "codex", "nome": c["titulo"], "detalhe": c["projeto"] + " · " + c["origem"],
                    "modelo": c["modelo"], "nivel": c["nivel"], "tarefa": "", "ha_segundos": c["ha_segundos"], "vivo": c["ha_segundos"] < VIVO_CLAUDE,
                    "cota_pts": c["cota_pts"], "tokens": c["tokens"]})
    nomes = {a["nome"] for a in out}
    pegas = {t["agente"]: t for t in db.tarefas("pega") if not t.get("auto")}
    ids_sessao = {"claude:" + x["id"] for x in ret.get("claude_sessoes", [])}
    for pl in db.pulsos_recentes(VIVO_PULSO):
        if pl["agente"] in ids_sessao:
            continue                      # o pulso do hook é da mesma sessão que já aparece pelo transcript
        if pl["agente"] not in nomes:
            t = pegas.get(pl["agente"])
            tit = coletores.titulo_por_prefixo(pl["agente"].split(":", 1)[-1]) if pl["agente"].startswith("claude:") else None
            out.append({"id": "pulso:" + pl["agente"], "tipo": "claude", "nome": tit or pl["agente"], "detalhe": pl["agente"] if tit else "", "modelo": "?", "nivel": t["nivel"] if t else 2,
                        "tarefa": (t["titulo"] if t else pl["nota"]), "ha_segundos": int(time.time() - pl["quando"]), "vivo": True})
    for a in out:
        t = pegas.get(a["nome"])
        if t and not a["tarefa"]:
            a["tarefa"] = t["titulo"]
    out.sort(key=lambda a: (not a["vivo"], a["ha_segundos"]))
    return out


def cartao_codex(ret, cs):
    """Cartão do Codex: aparece sempre, mesmo sem nada rodando (para não parecer desconectado)."""
    procs = ret.get("codex_processos", [])
    sess = ret.get("codex_sessoes", [])
    ultima = coletores.codex_ultima_execucao()
    cota = next((c for c in cs if c["agente"] == "codex"), None)
    ativas = [c for c in sess if c["ha_segundos"] < VIVO_CLAUDE]
    rodando = len(procs) or len(ativas)
    return {"estado": "rodando" if rodando else "parado", "rodando": rodando, "processos": len(procs), "sessoes_24h": len(sess),
            "ultima_execucao_s": int(time.time() - ultima) if ultima else None,
            "cota": ({"usado_pct": cota["usado_pct"], "disponivel_pct": cota["disponivel_pct"], "ritmo_atual": cota["ritmo_atual"],
                      "ritmo_unidade": cota["ritmo_unidade"], "previsao_fim_pct": cota["previsao_fim_pct"], "estoura": cota["estoura"],
                      "desatualizada": cota["desatualizada"], "leitura_velha": cota["leitura_velha"], "idade_leitura_s": cota["idade_leitura_s"],
                      "parado_sem_consumo": cota["parado_sem_consumo"]} if cota else None),
            "tokens_24h": {k: sum(c["tokens"].get(k, 0) for c in sess) for k in ("i", "o", "cr")},
            "pts_cota_24h": round(sum(c["cota_pts"] for c in sess), 1)}


def trabalhos_resumo(st):
    """Para a Visão: contagem por etapa e os concluídos de hoje com a hora de cada etapa."""
    if not st:
        return None
    por = {t["sessao"]: t for t in st["trabalhos"]}
    return {"contagem": st["contagem"], "gerado_em": st["gerado_em"], "concluidos_hoje": [{k: por[s][k] for k in ("titulo", "sessao", "pr", "etapas", "conferido_por", "ultima_frase")} for s in st["concluidos_hoje"] if s in por],
            "arquivar": st["arquivar"], "arquivadas_24h": st["arquivadas_24h"]}


def visao():
    ret = coletores.retrato()
    ag = agentes(ret)
    cs = db.cotas()
    ts = db.tarefas()
    contagem = {e: sum(1 for t in ts if t["estado"] == e) for e in db.ESTADOS}
    alertas = cotas_mod.alertas(cs, len(ret.get("codex_processos", [])))   # uma faixa por IA, só risco real de estouro
    itens_av = aovivo.fotografia()["itens"]
    st = trabalhos.ultimo()
    return {"codex": cartao_codex(ret, cs), "agentes": ag, "vivos": sum(1 for a in ag if a["vivo"]), "cotas": cs, "alertas": alertas,
            "tokens_hoje": ret.get("tokens_hoje", {}), "tokens_dias": ret.get("tokens_dias", {}),
            "tarefas_contagem": contagem, "mensagens": db.msgs(limite=8), "eventos": db.eventos(12),
            "agendadas": ret.get("agendadas", []), "coletado_em": ret.get("quando"), "erros": ret.get("erros", []),
            "ao_vivo": aovivo.fotografia()["resumo"], "economia_dia": custo.economia_dia(), "desperdicio": desperdicio.analisar(ret.get("claude_sessoes", [])) + desperdicio.executores_caros(),
            "contexto": painel.alertas_contexto(itens_av), "esperando": painel.esperando(itens_av, st), "resumo_dia": painel.resumo_dia(itens_av, cs, status=st), "trabalhos": trabalhos_resumo(st), "arquivador": trabalhos.arquivador(st=st) if st else None, "placar_auto": db.placar_executores(time.time() - 86400 * 7), "fila_espera": len(orcamento.fila(cs)), "melhorias_sugeridas": len(melhorias.lista("sugerida")), "gates_falhos": sellerturbo._cache["dados"].get("gates_falhos", 0), "placar": db.placar(), "versao": __version__, "agora": time.time()}


class Erro(Exception):
    def __init__(self, codigo, msg):
        self.codigo, self.msg = codigo, msg


def api_get(caminho, q):
    if caminho == "/api/visao":
        return visao()
    if caminho == "/api/ao-vivo":
        return aovivo.fotografia()
    if caminho == "/api/tarefas":
        tab = custo.carregar()
        ts = db.tarefas((q.get("estado") or [None])[0], (q.get("projeto") or [None])[0])
        auto = [t for t in ts if t.get("auto")]
        ts = [t for t in ts if not t.get("auto")] + auto[:int((q.get("auto") or [80])[0])]      # as automáticas são centenas: só as mais recentes
        cs = db.cotas()
        for t in ts:
            t["custo"] = custo.resumo(t, tab)
            t["espera"] = orcamento.em_espera(t, cs)
        return ts
    if caminho == "/api/seller-turbo":
        return sellerturbo.retrato()
    if caminho == "/api/vigia":
        return vigia.rodadas()
    if caminho == "/api/linha-do-tempo":
        return timeline.dia((q.get("dia") or [None])[0])
    if caminho == "/api/melhorias":
        return melhorias.lista((q.get("estado") or [None])[0])
    if caminho == "/api/desperdicio":
        return desperdicio.analisar(coletores.retrato().get("claude_sessoes", []))
    if caminho == "/api/fila":
        return orcamento.fila()
    if caminho == "/api/placar":
        return db.placar()
    if caminho == "/api/placar-executores":
        return db.placar_executores(time.time() - 86400 * 7)
    if caminho == "/api/executores":
        executores.sincronizar()
        return [t for t in db.tarefas() if t.get("auto")]
    if caminho == "/api/status":
        return trabalhos.status()
    if caminho == "/api/resumo-dia":
        return painel.resumo_dia(aovivo.fotografia()["itens"])
    if caminho == "/api/custos":
        return custos.relatorio((q.get("periodo") or ["semana"])[0], (q.get("por") or ["modelo"])[0])
    if caminho == "/api/economia":
        return {"economia_dia": custo.economia_dia(), "precos": custo.carregar()}
    if caminho == "/api/metricas":
        return metricas.resumo()
    if caminho == "/api/skills-custo":
        return skillcusto.relatorio((q.get("periodo") or ["semana"])[0])
    if caminho == "/api/mensagens":
        return db.msgs((q.get("agente") or [None])[0], limite=int((q.get("limite") or [100])[0]))
    if caminho == "/api/cotas":
        return db.cotas()
    if caminho == "/api/agentes":
        return agentes()
    if caminho == "/api/skills":
        sc = skillcusto.por_nome("semana")
        return [dict(x, custo=sc.get(x["nome"])) for x in skills.listar()]
    if caminho == "/api/projetos":
        return db.projetos()
    if caminho == "/api/recomendar":
        return cerebro.recomendar((q.get("q") or [""])[0], db.cotas(), db.placar())
    raise Erro(404, "rota inexistente")


def api_post(caminho, d):
    if caminho == "/api/tarefas":
        tid = db.tarefa_nova(d.get("titulo", ""), d.get("nivel", 2), d.get("projeto", ""), d.get("executor", ""), d.get("brief", ""),
                             d.get("tipo", ""), bool(d.get("quando_sobrar")))
        return {"id": tid}
    m = re.match(r"^/api/tarefas/(\d+)$", caminho)
    if m:
        tid, ac = int(m.group(1)), d.get("acao")
        if ac == "pegar":
            db.tarefa_pegar(tid, d.get("agente", ""))
        elif ac == "feita":
            db.tarefa_feita(tid, d.get("resultado", ""), d.get("agente"), d.get("tokens_in", 0), d.get("tokens_out", 0), d.get("modelo"))
        elif ac == "revisada":
            db.tarefa_revisada(tid, d.get("por", "dono"))
        elif ac == "voltou":
            db.tarefa_voltou(tid, d.get("motivo", "sem motivo"))
        elif ac == "gate":
            db.tarefa_gate(tid, bool(d.get("falhou", True)))
        elif ac == "bloqueada":
            db.tarefa_bloqueada(tid, d.get("motivo", "sem motivo"), d.get("agente"))
        elif ac in (None, "atualizar"):
            db.tarefa_atualizar(tid, **{k: d[k] for k in ("titulo", "nivel", "executor", "brief", "projeto") if k in d})
        else:
            raise Erro(400, "ação desconhecida")
        return {"ok": True}
    if caminho == "/api/mensagens" and d.get("acao") == "lidas":
        return {"marcadas": db.msgs_lidas()}
    if caminho == "/api/mensagens":
        return {"id": db.msg(d.get("de") or "dono", d.get("para") or "orquestrador", d.get("texto", ""))}
    if caminho == "/api/cotas":
        db.cota_registrar(d["agente"], d["usado"], d.get("renova"), "painel")
        return {"ok": True}
    if caminho == "/api/acoes":
        ac, iid, tid = d.get("acao"), d.get("id", ""), d.get("tarefa_id")
        if ac == "pausar":
            return acoes.pausar(iid, tid)
        if ac == "retomar":
            return acoes.retomar(iid, tid)
        if ac == "encerrar":
            return acoes.encerrar(iid, d.get("confirmar"))
        if ac == "redistribuir":
            return acoes.redistribuir(tid, d.get("executor", ""), d.get("titulo"), d.get("nivel", 2))
        raise Erro(400, "ação desconhecida")
    if caminho == "/api/melhorias":
        if d.get("acao") == "analisar":
            return {"novas": len(melhorias.analisar("semana" if d.get("periodo") == "semana" else "dia"))}
        raise Erro(400, "ação desconhecida")
    m = re.match(r"^/api/melhorias/(\d+)$", caminho)
    if m:
        ac = d.get("acao")
        if ac == "aprovar":
            return {"tarefa": melhorias.aprovar(int(m.group(1)))}
        if ac == "recusar":
            melhorias.recusar(int(m.group(1)), d.get("motivo", "")); return {"ok": True}
        if ac == "feita":
            melhorias.feita(int(m.group(1))); return {"ok": True}
        raise Erro(400, "ação desconhecida")
    if caminho == "/api/pulso":
        db.pulso(d.get("agente", "?"), d.get("nota", ""))
        return {"ok": True}
    if caminho == "/api/projetos":
        db.projeto_novo(d["nome"], d.get("descricao", ""))
        return {"ok": True}
    if caminho == "/api/recomendar":
        return cerebro.recomendar(d.get("descricao", ""), db.cotas(), db.placar())
    if caminho == "/api/skills/otimizar":
        return skillcusto.otimizar(d.get("nome", ""))
    if caminho == "/api/skills":
        if d.get("confirmar") is not True:
            raise Erro(400, "confirmação obrigatória (confirmar: true)")
        {"ativar": skills.ativar, "desativar": skills.desativar}.get(d.get("acao"), lambda n: (_ for _ in ()).throw(Erro(400, "ação inválida")))(d.get("nome"))
        return {"ok": True}
    raise Erro(404, "rota inexistente")


class H(BaseHTTPRequestHandler):
    server_version = "Orquestra"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        h = (self.headers.get("Host") or "").lower()
        return h in ("127.0.0.1:%d" % config.PORTA, "localhost:%d" % config.PORTA, "127.0.0.1", "localhost") or \
            re.match(r"^(127\.0\.0\.1|localhost):\d+$", h) is not None

    def _enviar(self, codigo, corpo, tipo="application/json; charset=utf-8"):
        if not isinstance(corpo, bytes):
            corpo = json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _tratar(self, metodo):
        if not self._host_ok():
            return self._enviar(403, {"erro": "host não permitido"})
        u = urlparse(self.path)
        try:
            if u.path in ("/api/custos.csv", "/api/custos.md") and metodo == "GET":
                qq = parse_qs(u.query)
                rel = custos.relatorio((qq.get("periodo") or ["semana"])[0], (qq.get("por") or ["modelo"])[0])
                return self._enviar(200, (custos.csv_texto(rel) if u.path.endswith("csv") else custos.markdown(rel)).encode(),
                                    ("text/csv" if u.path.endswith("csv") else "text/markdown") + "; charset=utf-8")
            if u.path == "/api/linha-do-tempo.md" and metodo == "GET":
                return self._enviar(200, timeline.markdown((parse_qs(u.query).get("dia") or [None])[0]).encode(), "text/markdown; charset=utf-8")
            if u.path.startswith("/api/"):
                if metodo == "GET":
                    return self._enviar(200, api_get(u.path, parse_qs(u.query)))
                org = self.headers.get("Origin")
                if org and not re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$", org):
                    raise Erro(403, "origem não permitida")
                if "json" not in (self.headers.get("Content-Type") or ""):
                    raise Erro(415, "use Content-Type: application/json")
                n = int(self.headers.get("Content-Length") or 0)
                if n > 1_000_000:
                    raise Erro(413, "corpo grande demais")
                dados = json.loads(self.rfile.read(n) or b"{}")
                return self._enviar(200, api_post(u.path, dados))
            if metodo != "GET":
                raise Erro(405, "método não permitido")
            rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            arq = os.path.normpath(os.path.join(config.pasta_web(), rel))
            if not arq.startswith(config.pasta_web() + os.sep) or not os.path.isfile(arq):
                raise Erro(404, "não encontrado")
            with open(arq, "rb") as f:
                return self._enviar(200, f.read(), (mimetypes.guess_type(arq)[0] or "application/octet-stream") + "; charset=utf-8")
        except Erro as e:
            self._enviar(e.codigo, {"erro": e.msg})
        except PermissionError as e:
            self._enviar(403, {"erro": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            self._enviar(400, {"erro": str(e.args[0] if e.args else e)})
        except Exception as e:  # noqa
            self._enviar(500, {"erro": "erro interno: %s" % e})

    def do_GET(self):
        self._tratar("GET")

    def do_POST(self):
        self._tratar("POST")


def vigias_em_loop():
    while True:
        try:
            if time.time() - sellerturbo._cache["quando"] > 120:
                sellerturbo.atualizar()
            desperdicio.verificar()
            orcamento.verificar()
            melhorias.rodada_diaria()
        except Exception as e:
            print("vigia falhou:", e, file=sys.stderr)
        time.sleep(30)


def status_em_loop():
    """Situação dos trabalhos a cada ≤ 60 s (arquivo status.json e Visão)."""
    while True:
        try:
            trabalhos.gravar_json()
        except Exception as e:
            print("status falhou:", e, file=sys.stderr)
        time.sleep(30)


def coletar_em_loop():
    while True:
        try:
            transcritos.atualizar()
            executores.sincronizar()
            coletores.coletar_tudo()
        except Exception as e:  # nunca morre
            print("coleta falhou:", e, file=sys.stderr)
        time.sleep(config.INTERVALO_COLETA)


def autopiloto_em_loop():
    while True:
        try:
            autopiloto.rodada()
        except Exception as e:
            print("autopiloto falhou:", e, file=sys.stderr)
        time.sleep(60)


def criar(porta=None):
    return ThreadingHTTPServer((config.HOST, porta if porta is not None else config.PORTA), H)


def main():
    srv = criar()
    threading.Thread(target=coletar_em_loop, daemon=True).start()
    threading.Thread(target=aovivo.loop, daemon=True).start()
    threading.Thread(target=vigias_em_loop, daemon=True).start()
    threading.Thread(target=status_em_loop, daemon=True).start()
    threading.Thread(target=autopiloto_em_loop, daemon=True).start()
    print("Orquestra em http://%s:%d" % (config.HOST, config.PORTA), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
