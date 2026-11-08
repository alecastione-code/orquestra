"""Quadro de trabalhos com situação detectada sozinha, sem o dono validar nada.
rodando → esperando dono → PR aberto → na fila → mesclado → no ar → conferido → terminado.
Fontes (todas só leitura): metadados do app (`local_*.json`), transcript (PR citado), `gh pr view`, deployments do GitHub
(o Railway publica o SUCCESS do commit lá; o CLI do Railway não está ligado a um projeto nesta máquina), `fila-unica.out` e o diário.
O Orquestra NÃO arquiva sessão: só diz quais podem ser arquivadas (`arquivar`) e registra o que foi arquivado (`orq arquivada`)."""
import glob
import json
import os
import re
import time
from datetime import datetime

from . import config, coletores, db, sellerturbo

REPO = "alecastione-code/seller-turbo-painel"
ETAPAS = ("rodando", "esperando_dono", "pr_aberto", "na_fila", "mesclado", "no_ar", "conferido", "terminado")
RE_PR_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PROTEGIDA = re.compile(r"continua[cç][aã]o|orquestrador|chat-m[aã]e|ferramentas|painel seller turbo", re.I)
RODANDO_S = 180          # transcript mexeu há menos de 3 min
RECENTE_S = 1200         # não arquivar o que mexeu nos últimos 20 min
ARQUIVADOR = "orquestra-arquivador"   # scheduledTaskId das rodadas da tarefa agendada que arquiva
RODADA_S, ATRASO_S = 1800, 4200       # roda a cada 30 min; alerta se passar de 70 min sem rodada
LINK_APP = "claude://claude.ai/epitaxy/"
CTX_PASSAGEM = 300_000
NAO_PASSOU_S = 1800      # 30 min depois do aviso sem sessão nova: pede o dono
JANELA_FILHA_S = 3600    # shortcut: a filha criada até 1 h antes do último aviso conta como a sessão nova; o hook só regrava o arquivo a cada +100k
_cache = {"pr": {}, "deploys": (0, [])}


def _raiz_lote():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "claude-lote-consertos-20261018")


def fila_path():
    return os.environ.get("ORQUESTRA_FILA_UNICA") or os.path.join(_raiz_lote(), "fila-unica.out")


def diario_path():
    return os.environ.get("ORQUESTRA_DIARIO") or os.path.join(_raiz_lote(), "PASSAGEM-2026-10-31.md")


def _ler(p, n=3_000_000):
    try:
        with coletores._abrir(p, encoding="utf-8", errors="replace") as f:
            return f.read(n)
    except (OSError, PermissionError):
        return ""


# ---------------------------------------------------------------- fontes
def fila_eventos(agora=None):
    """{PR: (epoch, texto)} do último evento de cada PR em fila-unica.out. As linhas só têm HH:MM: as datas saem de trás para frente."""
    agora = agora or time.time()
    linhas = [l for l in _ler(fila_path()).splitlines() if re.match(r"^\d\d:\d\d ", l)]
    dia, ant, out = datetime.fromtimestamp(agora).replace(hour=0, minute=0, second=0, microsecond=0), None, {}
    for l in reversed(linhas):
        h, m = int(l[:2]), int(l[3:5])
        if ant is not None and (h, m) > ant:      # o relógio "pulou" para a frente: é do dia anterior
            dia = datetime.fromtimestamp(dia.timestamp() - 86400)
        ant = (h, m)
        mm = re.search(r"#(\d+)\s+(.*)", l)
        if mm and int(mm.group(1)) not in out:
            out[int(mm.group(1))] = (dia.replace(hour=h, minute=m).timestamp(), mm.group(2).strip())
    return out


def conferencias(agora=None):
    """{PR: (epoch, quem)} das linhas do diário com `#N` e "conferid…"."""
    ano = datetime.fromtimestamp(agora or time.time()).year
    out = {}
    for l in _ler(diario_path()).splitlines():
        m = re.match(r"^- (\d\d)/(\d\d) (\d\d):(\d\d)\s", l)
        if not m or not re.search(r"conferid", l, re.I):
            continue
        quem = re.search(r"conferid\w*\s+(?:por|pelo|pela)\s+([\wÀ-ú ]{2,30})", l, re.I)
        t = datetime(ano, int(m.group(2)), int(m.group(1)), int(m.group(3)), int(m.group(4))).timestamp()
        for n in re.findall(r"#(\d+)", l):
            out[int(n)] = (t, (quem.group(1).strip() if quem else "orquestrador (diário)"))
    return out


def pr_info(numero, agora=None, repo=None):
    """{estado, mesclado_em, commit, url, rascunho} via `gh pr view`; mesclado/fechado fica em cache; None sem gh."""
    agora = agora or time.time()
    repo = repo or os.environ.get("ORQUESTRA_REPO", REPO)
    c = _cache["pr"].get((repo, numero))
    if c and (c[1]["estado"] != "OPEN" or agora - c[0] < 60):
        return c[1]
    try:
        d = sellerturbo._rodar(["pr", "view", str(numero), "--repo", repo, "--json", "state,mergedAt,createdAt,mergeCommit,isDraft,url,title"])
    except Exception:
        return c[1] if c else None
    info = {"repo": repo, "criado_em": db.parse_instante(d["createdAt"]) if d.get("createdAt") else None, "estado": d.get("state"), "rascunho": d.get("isDraft"), "url": d.get("url"), "titulo": d.get("title"),
            "commit": ((d.get("mergeCommit") or {}).get("oid") or "")[:40],
            "mesclado_em": db.parse_instante(d["mergedAt"]) if d.get("mergedAt") else None}
    _cache["pr"][(repo, numero)] = (agora, info)
    return info


def deploys(agora=None):
    """[(sha, epoch do SUCCESS)] dos deployments do GitHub (onde o Railway registra o resultado). Uma consulta GraphQL só (~0,6 s)."""
    agora = agora or time.time()
    if agora - _cache["deploys"][0] < 60:
        return _cache["deploys"][1]
    out = []
    try:
        dono, nome = os.environ.get("ORQUESTRA_REPO", REPO).split("/", 1)
        q = 'query{repository(owner:"%s",name:"%s"){deployments(last:30){nodes{commitOid statuses(first:20){nodes{state createdAt}}}}}}' % (dono, nome)
        for d in sellerturbo._rodar(["api", "graphql", "-f", "query=" + q])["data"]["repository"]["deployments"]["nodes"]:
            ok = [x for x in d["statuses"]["nodes"] if x.get("state") == "SUCCESS"]
            if ok:
                out.append((d["commitOid"], db.parse_instante(ok[-1]["createdAt"])))
    except Exception:
        return _cache["deploys"][1]
    _cache["deploys"] = (agora, out)
    return out


# ---------------------------------------------------------------- sessões do app
def sessoes_app(todas=False):
    """Sessões do app; `todas` inclui as arquivadas (para achar a sessão nova e as rodadas do arquivador)."""
    out = []
    for p in glob.glob(os.path.join(config.sessoes_app(), "*", "*", "local_*.json")):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("cliSessionId") and (todas or not d.get("isArchived")):
            out.append(d)
    return out


def _transcript(cli):
    for p in glob.glob(os.path.join(config.claude_home(), "projects", "*", cli + ".jsonl")):
        return p
    return None


def _turno_aberto(d, agora):
    """Mensagem do dono depois da última borda de turno e atividade recente: o turno ainda está em andamento (isRunning do app)."""
    try:
        return int(d.get("latestUserFrameAt") or 0) > int(d.get("lastTurnEdgeAt") or 0) and agora - int(d.get("lastActivityAt") or 0) / 1000.0 < 1800
    except (TypeError, ValueError):
        return False


def pergunta_real(d):
    """Só conta como "esperando dono" a sessão que parou perguntando ou bloqueada (resumo do turno); relatório final não conta.
    O resumo precisa ser do último turno."""
    ps = d.get("postTurnSummary") if isinstance(d.get("postTurnSummary"), dict) else {}
    if d.get("postTurnSummaryFor") and d.get("lastAssistantUuid") and d["postTurnSummaryFor"] != d["lastAssistantUuid"]:
        return False
    return bool(d.get("interruptedAwaitingPerson")) or ps.get("status_category") == "blocked" or bool(str(ps.get("needs_action") or "").strip())


def ultima_frase(d):
    ps = d.get("postTurnSummary") if isinstance(d.get("postTurnSummary"), dict) else {}
    w = d.get("turnWrapUp") if isinstance(d.get("turnWrapUp"), dict) else {}
    from .segredos import ocultar
    return ocultar(w.get("description") or ps.get("status_detail") or "", 300)


def _ramo(cli):
    p = _transcript(cli)
    if not p:
        return None
    info = coletores._ler_transcript(p, os.path.getmtime(p), os.path.getsize(p))
    return info.get("branch")


def pr_criado_no_transcript(cli):
    """(repo, número) do último PR que a sessão criou com `gh pr create` (lê o resultado da ferramenta); None se nunca criou."""
    p = _transcript(cli)
    if not p:
        return None
    criando, achado = set(), None
    for linha in _ler(p, 6_000_000).splitlines():
        if "gh pr create" not in linha and not criando:
            continue
        try:
            o = json.loads(linha)
        except ValueError:
            continue
        c = (o.get("message") or {}).get("content")
        for x in c if isinstance(c, list) else []:
            if x.get("type") == "tool_use" and "gh pr create" in str((x.get("input") or {}).get("command", "")):
                criando.add(x.get("id"))
            elif x.get("type") == "tool_result" and x.get("tool_use_id") in criando:
                txt = x.get("content") if isinstance(x.get("content"), str) else " ".join(str(y.get("text", "")) for y in (x.get("content") or []) if isinstance(y, dict))
                m = RE_PR_URL.findall(txt)
                if m:
                    achado = (m[-1][0], int(m[-1][1]))
    return achado


def pr_da_sessao(cli, titulo=""):
    """PR próprio da sessão: o PR cujo headRefName é o branch dela, ou o que ela criou com `gh pr create`.
    Orquestrador/mãe não tem PR próprio (só revisa). Devolve (repo, número) ou None."""
    if PROTEGIDA.search(titulo or ""):
        return None
    ramo = _ramo(cli)
    repo = os.environ.get("ORQUESTRA_REPO", REPO)
    if ramo and ramo not in ("main", "master", "HEAD"):
        try:
            prs = sellerturbo._rodar(["pr", "list", "--repo", repo, "--head", ramo, "--state", "all", "--json", "number", "--limit", "1"])
        except Exception:
            prs = []
        if prs:
            return repo, prs[0]["number"]
    return pr_criado_no_transcript(cli)


# ---------------------------------------------------------------- passar a vez
def _criado(d):
    return int(d.get("createdAt") or 0) // 1000


def avisos_passar_vez():
    """{id do CLI: (tokens, hora do último aviso)} de ~/.claude/passar-vez/<session_id>.json (gravado pelo hook global passar-vez-hook).
    O Orquestra só LÊ: o detector é o hook."""
    pasta = os.environ.get("ORQUESTRA_PASSAR_VEZ") or os.path.expanduser("~/.claude/passar-vez")
    out = {}
    try:
        nomes = os.listdir(pasta)
    except OSError:
        return out
    for n in nomes:
        if n.endswith(".json"):
            p = os.path.join(pasta, n)
            try:
                with open(p, encoding="utf-8") as f:
                    out[n[:-5]] = (int(json.load(f).get("tokens") or 0), os.path.getmtime(p))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
    return out


RE_LEIA = re.compile(r"Leia\s+`?([^`\s]*PASSAGEM[^`\s]*)")
_prompts = {}


def pasta_da_passagem(cli):
    """Pasta da PASSAGEM citada no 1º prompt "Leia …PASSAGEM…" da sessão (None se não começou assim). Não muda: fica em cache."""
    if cli not in _prompts:
        p = _transcript(cli)
        m = RE_LEIA.search(_ler(p, 40_000)) if p else None
        _prompts[cli] = os.path.dirname(m.group(1)) if m else None
    return _prompts[cli]


def _pastas(d):
    return {x for x in [d.get("cwd"), d.get("originCwd")] + list(d.get("writtenRepositories") or []) if x}   # shellFolders não: só abriu a pasta


def _e_nova_de(f, d):
    """0 = não continua a d; 2 = começou com "Leia …PASSAGEM…" de uma pasta onde a d trabalhou; 1 = só aberta por ela."""
    if f["sessionId"] == d["sessionId"]:
        return 0
    pasta = pasta_da_passagem(f["cliSessionId"])
    if pasta and pasta in _pastas(d):
        return 2
    return 1 if (f.get("spawnedFrom") or {}).get("sessionId") == d["sessionId"] else 0


def passagens(agora=None, todas=None):
    """Aviso de passagem em 3 estados para cada sessão aberta que passou de 300 mil:
    criando (o hook já mandou o chat criar a nova) → passou (existe sessão nova aberta por ela) → nao_passou (30 min sem nova: pede o dono)."""
    agora = agora or time.time()
    todas = sessoes_app(todas=True) if todas is None else todas
    av, out = avisos_passar_vez(), []
    for d in todas:
        tok, quando = av.get(d["cliSessionId"], (0, 0))
        if d.get("isArchived") or tok < CTX_PASSAGEM:
            continue
        nome = d.get("title") or "(sem título)"
        filhas = [f for f in todas if _criado(f) >= max(quando - JANELA_FILHA_S, _criado(d)) and _e_nova_de(f, d)]
        a = {"sessao": d["sessionId"], "titulo": nome, "contexto": tok, "nova": None, "link": None}
        if filhas:
            n = min(filhas, key=lambda f: (-_e_nova_de(f, d), _criado(f)))     # prompt de passagem primeiro; depois a 1ª criada
            a.update(estado="passou", nova=n["sessionId"], nova_titulo=n.get("title") or n["sessionId"], link=LINK_APP + n["sessionId"],
                     nova_turnos=int(n.get("completedTurns") or 0),
                     texto="%s passou a vez para %s às %s" % (nome, n.get("title") or n["sessionId"], datetime.fromtimestamp(_criado(n)).strftime("%H:%M")))
        elif agora - quando > NAO_PASSOU_S:
            a.update(estado="nao_passou", link=LINK_APP + d["sessionId"], texto="%s não passou a vez; abra o chat e clique no card" % nome)
        else:
            a.update(estado="criando", texto="%s passou de 300 mil (%d mil); ele mesmo está criando a sessão nova" % (nome, round(tok / 1000.0)))
        out.append(a)
    return sorted(out, key=lambda a: (("nao_passou", "criando", "passou").index(a["estado"]), -a["contexto"]))


# ---------------------------------------------------------------- regra de estado e de arquivamento
def classificar(rodando, pergunta, pr=None, na_fila=False, noar=None, conferido=None, outro_repo=False):
    """→ estado. `pr`: dict de pr_info ou None. A ordem é a do ciclo; rodando e pergunta real têm prioridade enquanto não há PR aberto."""
    if rodando:
        return "rodando"
    if pr and pr.get("estado") == "OPEN":
        return "na_fila" if na_fila else "pr_aberto"
    if pergunta:
        return "esperando_dono"
    if pr and pr.get("estado") == "MERGED":
        if outro_repo:           # só o PR do painel Seller Turbo exige conferência registrada no diário
            return "terminado"
        return "terminado" if conferido else "no_ar" if noar else "mesclado"
    return "terminado"


def arquivavel(t, fixadas=(), agora=None):
    """(sim/não, motivo). Com filha viva (não arquivada nem desligada) nunca. Quem passou a vez sai (menos fixada ou no meio de um turno). Fora isso, nunca: fixada, rodando, com PR aberto, esperando dono, orquestrador/mãe, ou mexida nos últimos 20 min."""
    agora = agora or time.time()
    if t["sessao"] in fixadas or t["cli"] in fixadas:
        return False, "fixada"
    if t.get("filha_viva"):             # arquivar a mãe arquiva junto as filhas paradas: espera elas se desligarem (detach) ou serem arquivadas
        return False, "sessão filha viva: %s" % t["filha_viva"]
    if t.get("passou_vez") and t["estado"] != "rodando":     # passou a vez: sai mesmo protegida por papel ou com PR (a sessão nova segue o trabalho)
        return True, "passou a vez para %s" % t["passou_vez"]
    if t["estado"] != "terminado":
        return False, "não terminou (%s)" % t["estado"]
    if t.get("pr") and t["pr"].get("estado") == "OPEN":
        return False, "PR aberto"
    if t.get("protegida"):
        return False, "orquestrador/mãe"
    if agora - (t.get("ultima_atividade") or 0) < RECENTE_S:
        return False, "mexeu há menos de 20 min"
    motivo = "PR #%d mesclado%s" % (t["pr"]["numero"], " e conferido" if t["etapas"].get("conferido") else "") if t.get("pr") else "terminada sem PR aberto"
    return True, motivo


# ---------------------------------------------------------------- varredura
OBSERVADAS = ("rodando", "esperando_dono", "na_fila", "terminado")


def _salvar(con, sessao, etapas):
    """Guarda só as etapas que o quadro OBSERVOU (hora da 1ª vez que viu). As que têm hora de fonte (PR criado, mesclado, no ar,
    conferido) vêm sempre da fonte e nunca ficam gravadas: assim um PR atribuído errado não deixa rastro."""
    con.execute("CREATE TABLE IF NOT EXISTS trabalhos_etapas (sessao TEXT PRIMARY KEY, etapas TEXT NOT NULL)")
    antigo = con.execute("SELECT etapas FROM trabalhos_etapas WHERE sessao=?", (sessao,)).fetchone()
    visto = {k: v for k, v in (json.loads(antigo[0]) if antigo else {}).items() if k in OBSERVADAS}
    for k in OBSERVADAS:
        if k in etapas:
            visto.setdefault(k, etapas[k])
    con.execute("INSERT OR REPLACE INTO trabalhos_etapas(sessao,etapas) VALUES (?,?)", (sessao, json.dumps(visto)))
    return {**visto, **{k: v for k, v in etapas.items() if k not in OBSERVADAS}}


def varrer(agora=None, fixadas=()):
    agora = agora or time.time()
    fila, conf, deps = fila_eventos(agora), conferencias(agora), deploys(agora)
    todas = sessoes_app(todas=True)
    passou = {p["sessao"]: p["nova_titulo"] for p in passagens(agora, todas) if p["estado"] == "passou" and p["nova_turnos"] >= 1}   # a nova já respondeu 1 turno
    filhas = {(f.get("spawnedFrom") or {}).get("sessionId"): f.get("title") or f["sessionId"]
              for f in todas if not f.get("isArchived") and not f.get("lineageDetached")}
    out = []
    with db.conectar() as con:
        for d in todas:
            if d.get("isArchived") or d.get("scheduledTaskId") == ARQUIVADOR:     # rodada do arquivador não é trabalho
                continue
            cli = d["cliSessionId"]
            arq = _transcript(cli)
            ult = os.path.getmtime(arq) if arq else (int(d.get("lastActivityAt") or 0) / 1000.0)
            rodando = agora - ult < RODANDO_S or _turno_aberto(d, agora)
            achado = pr_da_sessao(cli, d.get("title") or "")
            repo_pr, n = achado if achado else (None, None)
            pr = None
            if n:
                pr = pr_info(n, agora, repo_pr)
                if pr is not None:
                    pr = dict(pr, numero=n)
            outro = bool(repo_pr and repo_pr != os.environ.get("ORQUESTRA_REPO", REPO))
            na_fila = bool(n and not outro and n in fila and re.search(r"fila|reenfileirad|enfileirad|revisado", fila[n][1], re.I) and not re.search(r"MERGED|conflito|falhou", fila[n][1], re.I))
            noar = next((t for sha, t in deps if pr and pr.get("commit") and sha.startswith(pr["commit"][:7])), None)
            cf = conf.get(n) if n and not outro else None
            perg = pergunta_real(d)
            estado = classificar(rodando, perg, pr, na_fila, noar, cf, outro)
            etapas = {}
            if pr:
                etapas["pr_aberto"] = pr.get("criado_em") or ult
                if pr.get("mesclado_em"):
                    etapas["mesclado"] = pr["mesclado_em"]
                if noar:
                    etapas["no_ar"] = noar
                if cf:
                    etapas["conferido"] = cf[0]
            if estado in OBSERVADAS:
                etapas[estado] = agora
            etapas = _salvar(con, d["sessionId"], etapas)
            if estado == "terminado":      # PR de outro repositório termina ao mesclar; sem PR, na hora do fim do turno; do painel, ao conferir
                etapas["terminado"] = (pr or {}).get("mesclado_em") if outro and pr else cf[0] if cf else ult if not pr else etapas.get("terminado")
            t = {"titulo": d.get("title") or "(sem título)", "sessao": d["sessionId"], "cli": cli, "estado": estado, "ultima_atividade": ult,
                 "pr": ({"numero": n, "estado": pr["estado"], "url": pr.get("url"), "rascunho": pr.get("rascunho")} if pr else ({"numero": n, "estado": None} if n else None)),
                 "etapas": {k: v for k, v in etapas.items() if k in ETAPAS}, "conferido_por": cf[1] if cf else None,
                 "ultima_frase": ultima_frase(d),
                 "protegida": bool(PROTEGIDA.search(d.get("title") or "")), "passou_vez": passou.get(d["sessionId"]), "filha_viva": filhas.get(d["sessionId"])}
            t["arquivar"], t["arquivar_motivo"] = arquivavel(t, fixadas, agora)
            out.append(t)
    return out


def _iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def status(agora=None, fixadas=()):
    agora = agora or time.time()
    ts = varrer(agora, fixadas)
    hoje = datetime.fromtimestamp(agora).strftime("%Y-%m-%d")
    dia = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    for t in ts:
        t["etapas"] = {k: _iso(v) for k, v in t["etapas"].items()}
        t["ultima_atividade"] = _iso(t["ultima_atividade"])
    conc = [t for t in ts if t["estado"] == "terminado" and t["ultima_atividade"][:10] == hoje]
    with db.conectar() as con:
        _tabelas(con)
        arq = [dict(r) for r in con.execute("SELECT quando,sessao,motivo FROM arquivamentos WHERE quando>=? ORDER BY quando DESC", (agora - 86400,))]
    contagem = {e: sum(1 for t in ts if t["estado"] == e) for e in ETAPAS}
    return {"gerado_em": _iso(agora), "contagem": contagem, "trabalhos": ts, "concluidos_hoje": [t["sessao"] for t in conc],
            "arquivar": [{"sessao": t["sessao"], "titulo": t["titulo"], "motivo": t["arquivar_motivo"]} for t in ts if t["arquivar"]],
            "arquivadas_24h": [{"hora": _iso(a["quando"]), "sessao": a["sessao"], "motivo": a["motivo"]} for a in arq]}


def registrar_arquivada(sessao, motivo=""):
    """Linha de log (hora, sessão, motivo) de uma sessão que quem tem o poder de arquivar (list_sessions/archive_session) arquivou."""
    with db.conectar() as con:
        _tabelas(con)
        con.execute("INSERT INTO arquivamentos(quando,sessao,motivo) VALUES (?,?,?)", (time.time(), sessao, motivo))
        db.evento(con, "arquivada", "%s: %s" % (sessao, motivo))


_ultimo = {"status": None}


def ultimo():
    """Último status calculado pelo servidor (None antes da primeira varredura)."""
    return _ultimo["status"]


def gravar_json(agora=None):
    """Arquivo ~/Library/Application Support/Orquestra/status.json (atualizado a cada ≤ 60 s pelo servidor)."""
    s = status(agora)
    p = os.environ.get("ORQUESTRA_STATUS_JSON") or os.path.join(config.casa_orquestra(), "status.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    _ultimo["status"] = s
    return s


# ---------------------------------------------------------------- cartão do Arquivador
def _tabelas(con):
    con.execute("CREATE TABLE IF NOT EXISTS arquivamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, quando REAL NOT NULL, sessao TEXT NOT NULL, motivo TEXT DEFAULT '')")
    con.execute("CREATE TABLE IF NOT EXISTS status_tempos (id INTEGER PRIMARY KEY AUTOINCREMENT, quando REAL NOT NULL, segundos REAL NOT NULL)")


def registrar_tempo_status(segundos):
    with db.conectar() as con:
        _tabelas(con)
        con.execute("INSERT INTO status_tempos(quando,segundos) VALUES (?,?)", (time.time(), segundos))


def registrar_erro_arquivador(linha):
    from .segredos import ocultar
    with db.conectar() as con:
        db.evento(con, "arquivador_erro", ocultar(linha, 300))


def arquivador(agora=None, st=None):
    """Última rodada (criação da sessão da tarefa agendada), arquivadas nela, puladas com motivo, próxima rodada, atraso, erro e tempo do status."""
    agora = agora or time.time()
    rod = max((_criado(d) for d in sessoes_app(todas=True) if d.get("scheduledTaskId") == ARQUIVADOR), default=None)
    st = st or ultimo() or status(agora)
    with db.conectar() as con:
        _tabelas(con)
        arq = [dict(r) for r in con.execute("SELECT quando,sessao,motivo FROM arquivamentos WHERE quando>=? AND motivo LIKE '%arquivador automático%' ORDER BY quando", (rod or agora,))]
        tempo = con.execute("SELECT segundos,quando FROM status_tempos ORDER BY id DESC LIMIT 1").fetchone()
        erro = con.execute("SELECT quando,texto FROM eventos WHERE tipo='arquivador_erro' AND quando>=? ORDER BY id DESC LIMIT 1", (rod or 0,)).fetchone()
    return {"ultima": rod, "proxima": rod + RODADA_S if rod else None, "atrasado": rod is None or agora - rod > ATRASO_S,
            "arquivadas": [{"hora": _iso(a["quando"]), "sessao": a["sessao"], "motivo": a["motivo"]} for a in arq],
            "puladas": [{"titulo": t["titulo"], "motivo": t["arquivar_motivo"]} for t in st["trabalhos"] if t["estado"] == "terminado" and not t["arquivar"]],
            "erro": {"quando": erro[0], "texto": erro[1]} if erro else None,
            "tempo_status_s": tempo[0] if tempo else None, "tempo_medido_em": tempo[1] if tempo else None}
