"""Coletores: leem os arquivos reais de Codex e Claude. Só leitura; ignoram segredos; nada sai da máquina."""
import glob
import json
import os
import re
import subprocess
import time
from datetime import datetime

from . import config, db, skills
from .segredos import ocultar


def _proibido(caminho):
    base = os.path.basename(caminho).lower()
    return any(base == p or base.endswith(p) and p.startswith(".") for p in config.ARQUIVOS_PROIBIDOS)


def _abrir(caminho, *a, **k):
    """Único ponto de abertura de arquivos de dados: recusa nomes de segredo."""
    if _proibido(caminho):
        raise PermissionError("arquivo sensível ignorado: %s" % os.path.basename(caminho))
    return open(caminho, *a, **k)


# ---------------------------------------------------------------- Codex
RE_LIMITE = re.compile(r'"primary":\{"used_percent":([\d.]+),"window_minutes":(\d+),"resets_at":(\d+)')


def _cauda(caminho, n=262144):
    with _abrir(caminho, "rb") as f:
        f.seek(0, 2)
        tam = f.tell()
        f.seek(max(0, tam - n))
        return f.read().decode("utf-8", "replace")


def codex_cota(maximo_arquivos=8):
    base = os.path.join(config.codex_home(), "sessions")
    arqs = [p for p in glob.glob(os.path.join(base, "*", "*", "*", "rollout-*.jsonl")) if not _proibido(p)]
    arqs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for p in arqs[:maximo_arquivos]:
        achados = RE_LIMITE.findall(_cauda(p))
        if achados:
            u, janela, renova = achados[-1]
            return {"usado_pct": float(u), "janela_min": int(janela), "renova_em": float(renova),
                    "fonte": "codex:sessions", "lido_em": os.path.getmtime(p)}
    return None




def eh_codex_exec(cmd):
    """True para `…/codex exec …` e para `sh|bash|python3 …/codex exec …` (script com shebang). Não pega `sh -c '…codex exec…'`."""
    t = cmd.split()
    if len(t) >= 2 and t[0].endswith("codex") and t[1] == "exec":
        return True
    return len(t) >= 3 and os.path.basename(t[0]) in ("sh", "bash", "zsh", "python3", "node") and t[1].endswith("/codex") and t[2] == "exec"


def _titulo_processo(cmd):
    """Título do brief: primeira linha `# T…` do prompt (passado como argumento do codex exec)."""
    mm = re.search(r"# [TO][0-9]", cmd)
    i = mm.start() if mm else -1
    if i < 0:
        return None, None
    trecho = cmd[i:].split("\n")[0].split("\\012")[0]
    m = re.match(r"#\s*([TO][0-9A-Za-z]+)\s*[—–-]?\s*(.*)", trecho)
    if not m:
        return None, trecho[:100]
    return m.group(1), m.group(2).strip().strip('"“”')[:140]


def _ps():
    if "ORQUESTRA_PS" in os.environ:      # testes: nenhum processo real entra na conta
        return os.environ["ORQUESTRA_PS"]
    try:
        return subprocess.run(["ps", "-axww", "-o", "pid=,etime=,command="], capture_output=True, text=True,
                              timeout=10).stdout
    except Exception:
        return ""


def _etime(s):
    d = 0
    if "-" in s:
        d, s = s.split("-", 1)
    p = [int(x) for x in s.split(":")]
    while len(p) < 3:
        p.insert(0, 0)
    return int(d) * 86400 + p[0] * 3600 + p[1] * 60 + p[2]


def codex_processos(saida_ps=None):
    saida = []
    for linha in (saida_ps if saida_ps is not None else _ps()).splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+)\s+(.*)$", linha)
        if not m or not eh_codex_exec(m.group(3)):
            continue
        pid, et, cmd = int(m.group(1)), m.group(2), m.group(3)
        modelo = (re.search(r"\s-m\s+(\S+)", cmd) or [None, "?"])[1]
        pasta = (re.search(r"\s-C\s+(\S+)", cmd) or [None, ""])[1]
        tid, titulo = _titulo_processo(cmd)
        log = None
        if tid:
            cand = glob.glob(os.path.join(config.pasta_logs_codex(), tid + ".log")) + \
                   glob.glob(os.path.join(config.pasta_logs_codex(), tid + "-*.log"))
            if cand:
                log = max(cand, key=os.path.getmtime)
        saida.append({
            "pid": pid, "log": log, "ha_segundos": _etime(et), "modelo": modelo, "pasta": os.path.basename(pasta.rstrip("/")),
            "tarefa": tid, "titulo": titulo or "(sem título no brief)",
            "idade_log": int(time.time() - os.path.getmtime(log)) if log else None,
            "nivel": 1 if "luna" in modelo else 2})
    return saida


def _rollouts():
    base = os.path.join(config.codex_home(), "sessions")
    return [p for p in glob.glob(os.path.join(base, "*", "*", "*", "rollout-*.jsonl")) if not _proibido(p)]


def codex_sessoes(horas=24, agora_=None):
    """Sessões do Codex lidas dos rollouts (as do `codex exec` e as do app/CLI): título = 1º prompt curto sem segredos,
    modelo, última atividade, tokens e fatia da cota. Funciona mesmo sem nenhum processo rodando."""
    from . import custos, transcritos
    t = agora_ or time.time()
    fatias = None
    saida = []
    for p in _rollouts():
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if t - mt > horas * 3600:
            continue
        try:
            d = transcritos.dados_arquivo(p, "codex")
        except OSError:
            continue
        if fatias is None:
            fatias = custos.fatia_por_sessao()
        orig = d.get("originator") or ""
        saida.append({
            "id": d["sessao"][-8:], "sessao": d["sessao"], "titulo": d.get("titulo") or d.get("projeto") or "(sem título)",
            "projeto": d.get("projeto", ""), "pasta": d.get("cwd", ""), "modelo": d.get("modelo", "?"),
            "nivel": 1 if "luna" in d.get("modelo", "") else 2, "origem": "codex exec" if orig == "codex_exec" else "app/CLI do Codex",
            "originator": orig, "ultima_atividade": mt, "ha_segundos": int(t - mt), "tokens": d.get("tokens", {}),
            "cota_pts": fatias.get(d["sessao"], d.get("pct", 0.0))})
    saida.sort(key=lambda x: x["ultima_atividade"], reverse=True)
    return saida


_ultima_exec = {"t": 0, "v": None}


def codex_ultima_execucao(max_idade=30):
    """Hora (epoch) da última atividade de qualquer sessão do Codex, de todo o histórico (só stat dos arquivos)."""
    if time.time() - _ultima_exec["t"] < max_idade:
        return _ultima_exec["v"]
    mts = []
    for p in _rollouts():
        try:
            mts.append(os.path.getmtime(p))
        except OSError:
            pass
    _ultima_exec.update(t=time.time(), v=max(mts) if mts else None)
    return _ultima_exec["v"]


# ---------------------------------------------------------------- Claude
_cache_transcript = {}


def _nivel_modelo(m):
    m = (m or "").lower()
    if "haiku" in m:
        return 1
    if "sonnet" in m:
        return 2
    if "opus" in m or "fable" in m:
        return 3
    return 2


def _ler_transcript(caminho, mtime, tam):
    chave = caminho
    c = _cache_transcript.get(chave)
    if c and c["mtime"] == mtime and c["tam"] == tam:
        return c
    msgs, cwd, ultimo_modelo = {}, None, None
    titulo_custom, primeiro, branch = None, None, None
    with _abrir(caminho, encoding="utf-8", errors="replace") as f:
        for linha in f:
            if '"custom-title"' in linha:
                try:
                    titulo_custom = json.loads(linha).get("customTitle") or titulo_custom
                except ValueError:
                    pass
                continue
            quer_prompt = primeiro is None and ('"role":"user"' in linha or '"role": "user"' in linha)
            if '"usage"' not in linha and not quer_prompt and not (cwd is None and '"cwd"' in linha):
                continue
            try:
                o = json.loads(linha)
            except ValueError:
                continue
            if cwd is None and isinstance(o.get("cwd"), str):
                cwd = o["cwd"]
            if branch is None and o.get("gitBranch") not in (None, "", "HEAD"):
                branch = o["gitBranch"]
            if quer_prompt and isinstance(o.get("message"), dict) and not o.get("isMeta"):
                c = o["message"].get("content")
                t = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text") if isinstance(c, list) and not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c) else ""
                t = t.strip()
                if t and not t.startswith(("<", "[Request interrupted", "Caveat:")):
                    primeiro = ocultar(t.splitlines()[0], 70)
            m = o.get("message") if isinstance(o.get("message"), dict) else None
            if not m or not isinstance(m.get("usage"), dict):
                continue
            modelo = m.get("model")
            if not modelo or modelo.startswith("<"):
                continue
            u = m["usage"]
            ts = o.get("timestamp")
            try:
                dia = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
            except ValueError:
                dia = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            ultimo_modelo = modelo
            # a mesma mensagem aparece em várias linhas (streaming): vale a última
            msgs[m.get("id") or o.get("uuid") or len(msgs)] = (
                modelo, dia, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0))
    tokens = {}
    for modelo, dia, i, o, ca in msgs.values():
        t = tokens.setdefault(dia, {}).setdefault(modelo, {"entrada": 0, "saida": 0, "cache": 0})
        t["entrada"] += i; t["saida"] += o; t["cache"] += ca
    r = {"mtime": mtime, "tam": tam, "cwd": cwd, "modelo": ultimo_modelo, "tokens": tokens,
         "titulo_custom": titulo_custom, "primeiro_prompt": primeiro, "branch": branch}
    _cache_transcript[chave] = r
    return r


def _cauda_texto(caminho, n=262144):
    return _cauda(caminho, n)


def tokens_janela(arquivo, ini, fim):
    """Tokens de um transcript entre dois instantes (epoch): {modelo: {entrada, saida, cache}}. Lê só uso e hora."""
    msgs = {}
    with _abrir(arquivo, encoding="utf-8", errors="replace") as f:
        for linha in f:
            if '"usage"' not in linha:
                continue
            try:
                o = json.loads(linha)
                m = o["message"]
                ts = datetime.fromisoformat(str(o["timestamp"]).replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if not (ini <= ts <= fim) or not isinstance(m.get("usage"), dict) or not m.get("model"):
                continue
            u = m["usage"]
            msgs[m.get("id") or o.get("uuid") or len(msgs)] = (m["model"], int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0))
    tot = {}
    for modelo, i, o, c in msgs.values():
        t = tot.setdefault(modelo, {"entrada": 0, "saida": 0, "cache": 0})
        t["entrada"] += i; t["saida"] += o; t["cache"] += c
    return tot


_titulos_app = {"mtime": 0, "v": {}, "ids": {}}


def titulos_do_app():
    """{id da sessão CLI: título} lido dos metadados do app (só `cliSessionId` e `title`; o resto do arquivo é ignorado)."""
    base = config.sessoes_app()
    arqs = glob.glob(os.path.join(base, "*", "*", "local_*.json"))
    try:
        mt = max([os.path.getmtime(p) for p in arqs] + [0])
    except OSError:
        mt = 0
    if mt == _titulos_app["mtime"] and _titulos_app.get("base") == base:
        return _titulos_app["v"]
    v, ids = {}, {}
    for p in arqs:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("title") and d.get("cliSessionId"):
            v[str(d["cliSessionId"])] = str(d["title"])
            ids[str(d["cliSessionId"])] = os.path.splitext(os.path.basename(p))[0]      # local_<id>: o que o link do app abre
    _titulos_app.update(mtime=mt, v=v, ids=ids, base=base)
    return v


def id_app(sessao_id):
    """local_<id> da sessão no app Claude (para o link claude://claude.ai/epitaxy/local_<id>); None se o app não a conhece."""
    titulos_do_app()
    return _titulos_app["ids"].get(sessao_id)


def titulo_por_prefixo(prefixo):
    """Título pelo começo do id da sessão (os pulsos usam só 8 caracteres)."""
    return next((t for k, t in titulos_do_app().items() if k.startswith(prefixo)), None)


def titulo_da_sessao(sessao_id, info, pasta):
    """Título do app → título gravado no transcript → primeiro prompt curto → branch → pasta."""
    t = titulos_do_app().get(sessao_id)
    if t:
        return t, "app"
    if info.get("titulo_custom"):
        return info["titulo_custom"], "transcript"
    if info.get("primeiro_prompt"):
        return info["primeiro_prompt"], "prompt"
    if info.get("branch"):
        return info["branch"], "branch"
    return pasta, "pasta"


def claude_sessoes(horas=24, agora_=None):
    t = agora_ or time.time()
    base = os.path.join(config.claude_home(), "projects")
    sessoes = []
    for p in glob.glob(os.path.join(base, "*", "*.jsonl")):
        if _proibido(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        if t - st.st_mtime > horas * 3600:
            continue
        info = _ler_transcript(p, st.st_mtime, st.st_size)
        pasta = os.path.basename(os.path.dirname(p))
        cwd = info["cwd"] or pasta
        completo = os.path.splitext(os.path.basename(p))[0]
        titulo, origem = titulo_da_sessao(completo, info, os.path.basename(cwd.rstrip("/")) or pasta)
        sessoes.append({
            "id": completo[:8], "titulo": titulo, "titulo_origem": origem, "branch": info.get("branch"),
            "projeto": os.path.basename(cwd.rstrip("/")) or pasta,
            "pasta": cwd, "arquivo": p, "id_completo": os.path.splitext(os.path.basename(p))[0], "modelo": info["modelo"] or "?", "nivel": _nivel_modelo(info["modelo"]),
            "ultima_atividade": st.st_mtime, "ha_segundos": int(t - st.st_mtime), "tokens": info["tokens"]})
    sessoes.sort(key=lambda s: s["ultima_atividade"], reverse=True)
    return sessoes


def tokens_por_modelo(sessoes, dia=None):
    dia = dia or datetime.now().strftime("%Y-%m-%d")
    tot = {}
    for s in sessoes:
        for modelo, v in s["tokens"].get(dia, {}).items():
            t = tot.setdefault(modelo, {"entrada": 0, "saida": 0, "cache": 0})
            for k in t:
                t[k] += v[k]
    return tot


def tokens_por_dia(sessoes):
    tot = {}
    for s in sessoes:
        for dia, ms in s["tokens"].items():
            for modelo, v in ms.items():
                t = tot.setdefault(dia, {}).setdefault(modelo, {"entrada": 0, "saida": 0, "cache": 0})
                for k in t:
                    t[k] += v[k]
    return tot


def tarefas_agendadas():
    saida = []
    for md in sorted(glob.glob(os.path.join(config.claude_home(), "scheduled-tasks", "*", "SKILL.md"))):
        try:
            with _abrir(md, encoding="utf-8", errors="replace") as f:
                fm = skills.frontmatter(f.read(8000))
        except OSError:
            continue
        saida.append({"id": os.path.basename(os.path.dirname(md)), "nome": fm.get("name", ""),
                      "descricao": fm.get("description", "")[:300], "atualizada": os.path.getmtime(md)})
    return saida


# ---------------------------------------------------------------- tudo
_ultimo = {"quando": 0, "dados": {}}


def coletar_tudo():
    """Roda todos os coletores, grava a cota do Codex no banco e devolve o retrato."""
    d = {"quando": time.time(), "erros": []}
    try:
        c = codex_cota()
        d["codex_cota"] = c
        if c:
            db.cota_registrar("codex", c["usado_pct"], c["renova_em"], fonte=c["fonte"], janela_h=c["janela_min"] / 60.0, quando=c["lido_em"])
    except Exception as e:  # um coletor quebrado não derruba os outros
        d["erros"].append("codex_cota: %s" % e)
    for nome, fn in (("codex_processos", codex_processos), ("codex_sessoes", codex_sessoes), ("claude_sessoes", claude_sessoes),
                     ("agendadas", tarefas_agendadas), ("skills", skills.listar)):
        try:
            d[nome] = fn()
        except Exception as e:
            d[nome] = []
            d["erros"].append("%s: %s" % (nome, e))
    d["tokens_hoje"] = tokens_por_modelo(d["claude_sessoes"])
    d["tokens_dias"] = tokens_por_dia(d["claude_sessoes"])
    _ultimo.update(quando=d["quando"], dados=d)
    return d


def retrato(max_idade=75):
    if time.time() - _ultimo["quando"] > max_idade:
        return coletar_tudo()
    return _ultimo["dados"]
