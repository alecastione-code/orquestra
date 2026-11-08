"""Ao vivo: processo, CPU, memória, energia, etapa e estado (ativa/travada/pausada/…) de cada tarefa. Sem privilégio de administrador."""
import glob
import json
import os
import re
import subprocess
import threading
import time
from collections import deque

from . import config, coletores, db

ATIVA_CPU = 1.0          # % acima disso = ativa
LOG_ATIVO = 120          # log mudou há menos de 2 min = ativa
TRAVADA_LOG = 600        # log parado há mais de 10 min com CPU ~0 = travada
HIST_MAX = 120           # pontos de CPU guardados (≈6 min a 3 s)
FINALIZADA_RETEM = 3600

from .segredos import ocultar  # noqa: E402  (mantido aqui: outros módulos e testes usam aovivo.ocultar)


# ------------------------------------------------------------ ps / árvore
def parse_ps(texto):
    rows = []
    for l in texto.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(\d+)\s+(\S+)\s+(\S+)\s*(.*)$", l)
        if m:
            rows.append({"pid": int(m[1]), "ppid": int(m[2]), "cpu": float(m[3]), "rss_kb": int(m[4]),
                         "etime": coletores._etime(m[5]), "stat": m[6], "cmd": m[7]})
    return rows


def ps_arvore():
    try:
        return subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,pcpu=,rss=,etime=,stat=,command="],
                              capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def arvore(rows, raiz):
    por_pai = {}
    for r in rows:
        por_pai.setdefault(r["ppid"], []).append(r)
    mapa = {r["pid"]: r for r in rows}
    if raiz not in mapa:
        return []
    saida, fila = [], [mapa[raiz]]
    while fila:
        r = fila.pop()
        saida.append(r)
        fila.extend(por_pai.get(r["pid"], []))
    return saida


def energia_top(pids=None, timeout=12):
    """Impacto de energia do macOS por PID via `top` (sem sudo). Devolve {pid: power} ou None."""
    if os.environ.get("ORQUESTRA_SEM_TOP"):
        return None
    try:
        out = subprocess.run(["top", "-l", "2", "-s", "1", "-o", "power", "-n", "60", "-stats", "pid,power"],
                             capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return None
    ultimo = out.rsplit("PID", 1)[-1]
    mapa = {}
    for l in ultimo.splitlines():
        m = re.match(r"\s*(\d+)\s+([\d.]+)\s*$", l)
        if m:
            mapa[int(m[1])] = float(m[2])
    return mapa or None


def _fora_do_quadro():
    """cliSessionId das sessões do app que não são trabalho vivo: arquivadas e rodadas do arquivador. A rodada termina
    em archive_session("self") sem resultado no transcript, e sem isso o cartão virava "Travada" por até 2 h."""
    fora = set()
    for p in glob.glob(os.path.join(config.sessoes_app(), "*", "*", "local_*.json")):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("cliSessionId") and (d.get("isArchived") or d.get("scheduledTaskId") == "orquestra-arquivador"):
            fora.add(d["cliSessionId"])
    return fora


# ------------------------------------------------------------ classificação
def classificar(vivo=True, cpu=0.0, log_idade=None, stat="S", pausa="", aguardando_dono=False,
                codigo=None, tarefa_pendente=False):
    """→ (estado, motivo em uma linha). Estados: ativa, quieta, travada, pausada, esperando_dono, terminou, falhou."""
    if not vivo:
        if codigo is None:
            return "terminou", "processo saiu (código de saída não disponível)"
        if codigo == 0:
            return "terminou", "terminou com código 0"
        return "falhou", "falhou: processo saiu com código %s" % codigo
    if pausa:
        return "pausada", "pausada pelo agente: %s" % pausa
    if stat and stat.startswith("T"):
        return "pausada", "processo parado (SIGSTOP, estado T)"
    if aguardando_dono and (log_idade is None or log_idade >= 20):
        return "esperando_dono", "sessão terminou o turno e aguarda resposta do dono"
    if cpu is not None and cpu > ATIVA_CPU:
        return "ativa", "CPU %.0f%% (acima de %.0f%%)" % (cpu, ATIVA_CPU)
    if log_idade is not None and log_idade < LOG_ATIVO:
        return "ativa", "log/transcript mudou há %s" % _dur(log_idade)
    if log_idade is not None and log_idade > TRAVADA_LOG and (cpu is None or cpu <= ATIVA_CPU):
        return "travada", "CPU ~0 e %s parado há %s" % ("log" if cpu is not None else "transcript", _dur(log_idade))
    if log_idade is None:
        return "quieta", "vivo, CPU ~0, sem log para conferir"
    return "quieta", "CPU ~0, log parado há %s (ainda dentro dos 10 min)" % _dur(log_idade)


def _dur(s):
    s = int(s)
    return "%d s" % s if s < 60 else "%d min" % (s // 60) if s < 3600 else "%d h %d min" % (s // 3600, s % 3600 // 60)


# ------------------------------------------------------------ etapa (log / transcript)
def _cauda(caminho, n):
    try:
        with coletores._abrir(caminho, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


RUIDO = re.compile(r"^\s*(succeeded|failed|exited|-{3,}|tokens used|\d[\d,]*$|user$|codex$|thinking$)", re.I)


def etapa_codex(log):
    if not log:
        return ""
    linhas = [l for l in _cauda(log, 12000).split("\n") if l.strip()]
    for i in range(len(linhas) - 1, max(-1, len(linhas) - 80), -1):
        if linhas[i].strip() == "exec" and i + 1 < len(linhas):
            return "exec: " + ocultar(linhas[i + 1], 140)
    for l in reversed(linhas):
        if not RUIDO.match(l):
            return ocultar(l, 150)
    return ""


def etapa_claude(arquivo):
    """Última ação do transcript e se a sessão aguarda o dono / tem ferramenta pendente."""
    r = {"etapa": "", "aguardando_dono": False, "pendente": False, "contexto": None}
    linhas = _cauda(arquivo, 98304).split("\n")
    for l in reversed(linhas):
        if not l.strip():
            continue
        try:
            o = json.loads(l)
        except ValueError:
            continue
        m = o.get("message") if isinstance(o.get("message"), dict) else None
        if not m:
            continue
        c = m.get("content")
        if m.get("role") == "user":
            if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                continue  # resultado de ferramenta: segue procurando a ação que o gerou
            continue
        if m.get("role") != "assistant" or not isinstance(c, list):
            continue
        u = m.get("usage") if isinstance(m.get("usage"), dict) else None
        if u and r["contexto"] is None:      # tamanho do contexto = o que a última resposta leu (entrada + cache)
            r["contexto"] = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
        for x in reversed(c):
            if x.get("type") == "tool_use":
                i = x.get("input") or {}
                det = i.get("description") or i.get("command") or i.get("file_path") or i.get("path") or i.get("pattern") or ""
                r["etapa"] = ocultar("%s: %s" % (x.get("name"), det), 150)
                r["pendente"] = _sem_resultado(linhas, x.get("id"))
                return r
            if x.get("type") == "text" and x.get("text", "").strip():
                r["etapa"] = ocultar(x["text"], 150)
                r["aguardando_dono"] = m.get("stop_reason") in ("end_turn", None) and not r["pendente"]
                return r
    return r


def _sem_resultado(linhas, tool_id):
    return bool(tool_id) and not any(tool_id in l and "tool_result" in l for l in linhas[-60:])


# ------------------------------------------------------------ amostrador
class Amostrador:
    def __init__(self):
        self.hist = {}            # chave -> deque[(t, cpu)]
        self.energia = {}         # pid -> power
        self.energia_t = 0
        self.vistos = {}          # chave -> último item vivo
        self.finalizados = {}     # chave -> item terminado/falhou
        self.estados = {}         # chave -> último estado (para alertar na virada)
        self.ultima = {"quando": 0, "itens": [], "resumo": {}}
        self.cpu_s = {}           # (dia, chave) -> [tarefa_id, segundos de CPU] ainda não gravados
        self.t_ant = None
        self.t_flush = 0
        self.lock = threading.Lock()

    def _atualiza_energia(self, agora):
        if agora - self.energia_t > 15:
            self.energia_t = agora
            m = energia_top()
            if m is not None:
                self.energia = m
                self.energia_ok = True
            else:
                self.energia_ok = False

    energia_ok = None

    def amostrar(self, ps_txt=None, agora=None, sessoes=None, buscar_energia=True):
        with self.lock:
            return self._amostrar(ps_txt, agora, sessoes, buscar_energia)

    def _amostrar(self, ps_txt, agora, sessoes, buscar_energia):
        agora = agora or time.time()
        ps_txt = ps_txt if ps_txt is not None else ps_arvore()
        rows = parse_ps(ps_txt)
        if buscar_energia:
            self._atualiza_energia(agora)
        try:
            from . import vigia
            vigia.verificar(rows, agora)
        except Exception:
            pass
        tarefas = db.tarefas()
        pegas = [t for t in tarefas if t["estado"] in ("pega", "nova") and t["agente"] and not t.get("auto")]
        pulsos = {p["agente"]: p for p in db.pulsos_recentes(900)}
        itens, vivos = [], set()

        # --- Codex
        for p in coletores.codex_processos(_so_codex(ps_txt) if ps_txt else ""):
            chave = "codex:%d" % p["pid"]
            vivos.add(chave)
            arv = arvore(rows, p["pid"])
            cpu = round(sum(r["cpu"] for r in arv), 1)
            rss = sum(r["rss_kb"] for r in arv) // 1024
            raiz = next((r for r in arv if r["pid"] == p["pid"]), {})
            h = self.hist.setdefault(chave, deque(maxlen=HIST_MAX))
            h.append((agora, cpu))
            tarefa = self._vincula(tarefas, p)
            log = p.get("log") or (tarefa or {}).get("log") or ""
            log_idade = int(agora - os.path.getmtime(log)) if log and os.path.exists(log) else p.get("idade_log")
            estado, motivo = classificar(True, cpu, log_idade, raiz.get("stat", "S"), (tarefa or {}).get("pausa", ""))
            it = self._item(chave, "codex", p["pasta"], p["modelo"], p["nivel"], tarefa, p["titulo"], p["pid"],
                            [r["pid"] for r in arv], cpu, rss, p["ha_segundos"], etapa_codex(log), estado, motivo,
                            log_idade, agora)
            it["tarefa_ref"] = p["tarefa"]
            it["log"] = log
            itens.append(it)

        # --- sessões do Codex lidas dos rollouts (app/CLI ou `codex exec` já terminado): sem PID, então nunca "travada"
        pastas_vivas = {i["nome"] for i in itens if i["tipo"] == "codex"}
        for c in coletores.codex_sessoes(2, agora):
            if c["ha_segundos"] < 600 and c["projeto"] in pastas_vivas:
                continue
            chave = "codex-sessao:" + c["id"]
            vivos.add(chave)
            ativa = c["ha_segundos"] < LOG_ATIVO
            it = self._item(chave, "codex", c["titulo"], c["modelo"], c["nivel"], None, "", None, [], None, None, c["ha_segundos"], "",
                            "ativa" if ativa else "quieta",
                            "rollout mudou há %s" % _dur(c["ha_segundos"]) if ativa else "sem processo ativo; última atividade há %s" % _dur(c["ha_segundos"]),
                            c["ha_segundos"], agora)
            it["detalhe"] = c["projeto"] + " · " + c["origem"]
            it["cota_pts"] = c["cota_pts"]
            itens.append(it)

        # --- sessões Claude (transcript; sem PID conhecido)
        fora = _fora_do_quadro()
        for s in (sessoes if sessoes is not None else coletores.retrato().get("claude_sessoes", [])):
            arq = s.get("arquivo")
            if not arq or not os.path.exists(arq) or s.get("id_completo") in fora:
                continue
            idade = int(agora - os.path.getmtime(arq))
            e = etapa_claude(arq)
            if idade > 7200 and not e["aguardando_dono"]:      # parada e sem nada pendente do dono: fora; esperando o dono fica até sair das 24 h
                continue
            chave = "claude:" + s["id"]
            vivos.add(chave)
            pend = e["pendente"]
            estado, motivo = classificar(True, None, idade, "S", "", e["aguardando_dono"], None, pend)
            if estado == "quieta" and pend and idade <= TRAVADA_LOG:
                motivo = "ferramenta rodando há %s (dentro dos 10 min)" % _dur(idade)
            tarefa = next((t for t in pegas if t["agente"] in (s["projeto"], s["id"], "claude:" + s["id"])), None)
            try:
                nasc = int(agora - os.stat(arq).st_birthtime)
            except (OSError, AttributeError):
                nasc = None
            it = self._item(chave, "orquestrador" if s["nivel"] == 3 else "claude", s.get("titulo") or s["projeto"], s["modelo"], s["nivel"],
                            tarefa, "", None, [], None, None, nasc, e["etapa"], estado, motivo, idade, agora)
            it["log"] = arq
            it["contexto"] = e["contexto"]
            it["pasta"] = s.get("pasta")
            it["id_completo"] = s.get("id_completo")
            it["app_id"] = coletores.id_app(s.get("id_completo") or "")
            it["ultima_frase"] = e["etapa"] if e["aguardando_dono"] else ""
            it["detalhe"] = s["projeto"] + (" · " + s["branch"] if s.get("branch") else "")
            self.hist.setdefault(chave, deque(maxlen=HIST_MAX)).append((agora, 1.0 if estado == "ativa" else 0.0))
            it["cpu_hist"] = self._hist(chave)
            itens.append(it)

        # --- tarefas do quadro "pega" sem processo (agente que só dá pulso / script)
        usados = {i["tarefa_id"] for i in itens if i.get("tarefa_id")}
        for t in tarefas:
            if t["estado"] != "pega" or t["id"] in usados or t.get("auto"):      # automáticas já aparecem como sessão/processo
                continue
            pl = pulsos.get(t["agente"])
            idade = int(agora - pl["quando"]) if pl else None
            pid = t.get("pid")
            existe = pid and any(r["pid"] == pid for r in rows)
            if pid and not existe:
                continue  # processo vinculado já saiu: tratado abaixo como finalizado
            estado, motivo = classificar(True, None, idade, "S", t["pausa"])
            if idade is None:
                estado, motivo = "quieta", "sem pulso nem processo para conferir"
            if t["projeto"] == "vigia":
                estado, motivo = "ativa", "rodada do vigia em execução (nível 0)"
            it = self._item("tarefa:%d" % t["id"], "agente", t["agente"], t["modelo"] or "?", t["nivel"], t, "", pid,
                            [pid] if pid else [], None, None, None, (pl or {}).get("nota", ""), estado, motivo, idade, agora)
            itens.append(it)
            vivos.add(it["id"])

        # --- quem sumiu: terminou ou falhou
        for chave, ult in list(self.vistos.items()):
            if chave not in vivos and chave not in self.finalizados and not chave.startswith("claude:"):
                cod = _codigo_saida(ult.get("log"))
                try:
                    tarefa = db.tarefa(ult["tarefa_id"]) if ult.get("tarefa_id") else None
                except KeyError:           # a tarefa foi removida/trocada de banco: não derruba a visão inteira
                    tarefa = None
                if tarefa and tarefa["estado"] in ("feita", "aguardando") and cod is None:
                    cod = 0
                estado, motivo = classificar(False, 0, None, "", "", False, cod)
                fin = dict(ult, estado=estado, motivo=motivo, finalizada_em=agora, vivo=False)
                self.finalizados[chave] = fin
        for chave in list(self.finalizados):
            if agora - self.finalizados[chave]["finalizada_em"] > FINALIZADA_RETEM:
                del self.finalizados[chave]
        for chave in list(self.vistos):
            if chave not in vivos:
                del self.vistos[chave]
        for it in itens:
            self.vistos[it["id"]] = it
        itens += list(self.finalizados.values())
        for chave in list(self.hist):
            if chave not in vivos and chave not in self.finalizados:
                del self.hist[chave]

        self._acumular_cpu(itens, agora)
        self._alertar(itens)
        itens.sort(key=lambda i: ({"travada": 0, "falhou": 0, "esperando_dono": 1, "ativa": 2, "quieta": 3, "pausada": 3}.get(i["estado"], 4), i.get("tempo_s") or 0))
        resumo = {k: sum(1 for i in itens if i["estado"] == k) for k in
                  ("ativa", "travada", "pausada", "esperando_dono", "quieta", "terminou", "falhou")}
        self.ultima = {"quando": agora, "itens": itens, "resumo": resumo,
                       "energia": "medida (top)" if self.energia_ok else "estimativa (CPU% × tempo)"}
        return self.ultima

    def _acumular_cpu(self, itens, agora):
        """CPU-tempo por tarefa/agente (base do custo de máquina). Grava no banco a cada 60 s."""
        dt = min(agora - self.t_ant, 30) if self.t_ant else 0
        self.t_ant = agora
        dia = time.strftime("%Y-%m-%d", time.localtime(agora))
        for i in itens:
            if i.get("vivo") and i.get("cpu"):
                k = (dia, i["id"])
                v = self.cpu_s.setdefault(k, [i.get("tarefa_id"), 0.0])
                v[1] += i["cpu"] / 100.0 * dt
        if agora - self.t_flush > 60 and self.cpu_s:
            self.t_flush = agora
            try:
                with db.conectar() as con:
                    for (d, chave), (tid, seg) in self.cpu_s.items():
                        con.execute("INSERT INTO custos_maquina(dia,chave,tarefa_id,cpu_s) VALUES (?,?,?,?) "
                                    "ON CONFLICT(dia,chave) DO UPDATE SET cpu_s=cpu_s+excluded.cpu_s, tarefa_id=COALESCE(excluded.tarefa_id, tarefa_id)",
                                    (d, chave, tid, seg))
                self.cpu_s.clear()
            except Exception:
                pass

    @staticmethod
    def _vincula(tarefas, p):
        for t in tarefas:
            if t["estado"] not in ("pega", "nova", "aguardando"):
                continue
            if t.get("pid") == p["pid"] or t["agente"] in (p.get("tarefa"), "codex:%d" % p["pid"]) and t["agente"]:
                return t
        return None

    def _hist(self, chave):
        h = list(self.hist.get(chave, []))
        return [[round(t), c] for t, c in h[-60:]]

    def _item(self, chave, tipo, nome, modelo, nivel, tarefa, titulo, pid, pids, cpu, rss, etime, etapa, estado, motivo,
              log_idade, agora):
        ini = (tarefa or {}).get("iniciado")
        tempo = int(agora - ini) if ini else etime
        media = db.duracao_media(nivel, (tarefa or {}).get("tipo", "")) if tarefa or tipo == "codex" else None
        eta = None
        if media and tempo is not None and estado in ("ativa", "quieta", "travada", "pausada", "esperando_dono"):
            eta = max(0, int(media - tempo))
        energia = None
        if pids:
            if self.energia_ok and self.energia:
                energia = {"valor": round(sum(self.energia.get(p, 0.0) for p in pids), 1), "estimativa": False}
            elif cpu is not None and tempo:
                energia = {"valor": round(cpu * tempo / 100.0), "estimativa": True, "unidade": "CPU-s (estimativa: CPU% × tempo)"}
        return {"id": chave, "tipo": tipo, "nome": nome, "modelo": modelo, "nivel": nivel,
                "tarefa_id": (tarefa or {}).get("id"), "titulo": (tarefa or {}).get("titulo") or titulo,
                "pid": pid, "processos": len(pids), "cpu": cpu, "rss_mb": rss, "energia": energia,
                "etapa_agente": (tarefa or {}).get("etapa", ""), "etapa": etapa, "estado": estado, "motivo": motivo,
                "tempo_s": tempo, "eta_s": eta, "eta_texto": ("em ~" + _dur(eta)) if eta else (
                    "passou da média" if media and tempo and tempo > media else "sem estimativa"),
                "ultimo_movimento_s": log_idade, "cpu_hist": self._hist(chave), "vivo": True}

    def _alertar(self, itens):
        for i in itens:
            ant = self.estados.get(i["id"])
            self.estados[i["id"]] = i["estado"]
            if i["estado"] in ("travada", "falhou") and ant != i["estado"] and ant is not None or \
               (i["estado"] == "falhou" and ant is None):
                txt = "%s %s (%s): %s" % ("TRAVADA" if i["estado"] == "travada" else "FALHOU", i["nome"], i["id"], i["motivo"])
                if i.get("titulo"):
                    txt += " — " + i["titulo"][:80]
                try:
                    with db.conectar() as con:
                        db.evento(con, "alerta", txt)
                    db.msg("orquestra", "orquestrador", txt)
                    from . import notificar
                    notificar.enviar("Orquestra", txt)
                except Exception:
                    pass


def _so_codex(ps_txt):
    """codex_processos espera `pid etime command`; converte do formato rico."""
    out = []
    for l in ps_txt.splitlines():
        m = re.match(r"\s*(\d+)\s+\d+\s+[\d.]+\s+\d+\s+(\S+)\s+\S+\s*(.*)$", l)
        if m:
            out.append("%s %s %s" % (m[1], m[2], m[3]))
    return "\n".join(out)


def _codigo_saida(log):
    if not log:
        return None
    m = re.findall(r"\bexit (\d+)\b", _cauda(log, 4000))
    return int(m[-1]) if m else None


AMOSTRADOR = Amostrador()


def fotografia(max_idade=6):
    if time.time() - AMOSTRADOR.ultima["quando"] > max_idade:
        return AMOSTRADOR.amostrar()
    return AMOSTRADOR.ultima


def loop(intervalo=3):
    while True:
        try:
            AMOSTRADOR.amostrar()
        except Exception as e:  # nunca morre
            import sys
            print("ao-vivo falhou:", e, file=sys.stderr)
        time.sleep(intervalo)
