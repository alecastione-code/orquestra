"""Lê transcripts do Claude (e rollouts do Codex) UMA vez por mudança e guarda contagens: tokens, skills, perguntas do dono.
Texto de mensagem só de EXECUTORES (subagentes e rollouts do Codex): título e resultado curtos, com segredo redigido."""
import glob
import json
import os
import re
import time
from datetime import datetime

from . import config, coletores, db
from .segredos import ocultar

PERGUNTA = re.compile(r"\b(e a[ií]|eai|como t[aá]|como est[aá]|j[aá] terminou|cad[eê]|status|andamento)\b", re.I)
RE_SKILL_PATH = re.compile(r"/skills/([^/]+)/(SKILL\.md|references/.+|scripts/.+)$")
RE_CMD = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
BYTES_POR_TOKEN = 3.5
VERSAO_CLAUDE = 4  # 4: início/fim, título e resultado (subagentes) e tamanho do contexto; 3: baldes de 1 min por modelo
VERSAO_CODEX = 5   # 5: fim e resultado do rollout; 2: guarda a série (hora, % usado) para projetar pelo ritmo das últimas 24 h


def _dia(ts, padrao):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return padrao


def _tamanho_skill(nome, rel="SKILL.md", caminho=None):
    if caminho and os.path.isfile(caminho):
        return os.path.getsize(caminho)
    base = nome.split(":")[-1]
    for raiz in (os.path.expanduser("~/.agents/skills"), config.skills_ativas(), config.skills_desativadas()):
        p = os.path.join(raiz, base, rel)
        if os.path.isfile(p):
            return os.path.getsize(p)
    return 0


def _texto_usuario(m):
    """Texto de uma mensagem de usuário que NÃO é resultado de ferramenta; '' caso contrário."""
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            return ""
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def analisar_claude(caminho):
    base_dia = datetime.fromtimestamp(os.path.getmtime(caminho)).strftime("%Y-%m-%d")
    recs, cwd, ids, perguntas = [], None, {}, {}
    ext = {"t_ini": None, "t_fim": None, "prompt": None, "resultado": None, "ctx": 0}
    with coletores._abrir(caminho, encoding="utf-8", errors="replace") as f:
        for linha in f:
            if not ('"usage"' in linha or '"role":"user"' in linha or '"role": "user"' in linha or (cwd is None and '"cwd"' in linha)):
                continue
            try:
                o = json.loads(linha)
            except ValueError:
                continue
            if cwd is None and isinstance(o.get("cwd"), str):
                cwd = o["cwd"]
            m = o.get("message") if isinstance(o.get("message"), dict) else None
            if not m:
                continue
            dia = _dia(o.get("timestamp"), base_dia)
            try:
                t_ep = datetime.fromisoformat(str(o.get("timestamp")).replace("Z", "+00:00")).timestamp()
                ext["t_ini"] = t_ep if ext["t_ini"] is None else min(ext["t_ini"], t_ep)
                ext["t_fim"] = t_ep if ext["t_fim"] is None else max(ext["t_fim"], t_ep)
            except ValueError:
                pass
            if m.get("role") == "assistant":
                c = m.get("content") if isinstance(m.get("content"), list) else []
                u = m.get("usage") if isinstance(m.get("usage"), dict) else None
                mid = m.get("id") or o.get("uuid")
                if u and m.get("model") and not str(m["model"]).startswith("<"):
                    try:
                        ts_ep = datetime.fromisoformat(str(o.get("timestamp")).replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        ts_ep = os.path.getmtime(caminho)
                    reg = {"ts": ts_ep, "modelo": m["model"], "dia": dia, "i": int(u.get("input_tokens") or 0), "o": int(u.get("output_tokens") or 0),
                           "cr": int(u.get("cache_read_input_tokens") or 0), "cw": int(u.get("cache_creation_input_tokens") or 0)}
                    ext["ctx"] = reg["i"] + reg["cr"] + reg["cw"]
                    if mid in ids:
                        recs[ids[mid]][1] = reg  # streaming repete a mensagem: vale a última
                    else:
                        ids[mid] = len(recs)
                        recs.append(["asst", reg])
                for x in c:
                    if x.get("type") == "text" and str(x.get("text", "")).strip():
                        ext["resultado"] = x["text"]
                    if x.get("type") != "tool_use":
                        continue
                    inp = x.get("input") or {}
                    if x.get("name") == "Skill" and inp.get("skill"):
                        recs.append(["skill", str(inp["skill"]), "ferramenta", dia, 0])
                    elif x.get("name") == "Read":
                        mm = RE_SKILL_PATH.search(str(inp.get("file_path", "")))
                        if mm:
                            recs.append(["leitura", mm.group(1), mm.group(2), dia, _tamanho_skill(mm.group(1), mm.group(2), inp.get("file_path"))])
            elif m.get("role") == "user":
                t = _texto_usuario(m)
                if not t.strip() or o.get("isMeta"):
                    continue
                if ext["prompt"] is None and not t.lstrip().startswith("<"):
                    ext["prompt"] = t
                cmd = RE_CMD.search(t)
                if cmd:
                    recs.append(["skill", cmd.group(1), "comando-dono", dia, 0])
                    continue
                if t.lstrip().startswith(("<", "[Request interrupted", "Caveat:")):
                    continue
                recs.append(["turno", dia])
                if len(t) <= 200 and PERGUNTA.search(t):
                    perguntas[dia] = perguntas.get(dia, 0) + 1
    r = _consolidar(caminho, cwd, recs, perguntas)
    r["contexto"] = ext["ctx"]
    r["inicio"], r["fim"] = ext["t_ini"], ext["t_fim"]
    if r["subagente"]:      # texto só de executor: curto e sem segredo
        r["prompt1"] = next((ocultar(l.strip().lstrip("#").strip(), 300) for l in str(ext["prompt"] or "").splitlines() if l.strip()), "")
        r["resultado"] = _tres_linhas(ext["resultado"])
    return r


def _tres_linhas(txt, largura=200):
    """As 3 primeiras linhas não vazias da mensagem final, cada uma sem segredo e com no máximo `largura` caracteres."""
    ls = [l.strip() for l in str(txt or "").splitlines() if l.strip()]
    return "\n".join(ocultar(l, largura) for l in ls[:3])


def _consolidar(caminho, cwd, recs, perguntas):
    dias, skills_ev, atual, lidos, baldes = {}, [], None, {}, {}
    for r in recs:
        if r[0] == "asst":
            g = r[1]
            d = dias.setdefault(g["dia"], {}).setdefault(g["modelo"], {"n": 0, "i": 0, "o": 0, "cr": 0, "cw": 0})
            d["n"] += 1
            for k in ("i", "o", "cr", "cw"):
                d[k] += g[k]
            b = baldes.setdefault("%d|%s" % (int(g["ts"] // 60) * 60, g["modelo"]), [0, 0, 0, 0])
            for n_, k in enumerate(("i", "o", "cr", "cw")):
                b[n_] += g[k]
            if atual:
                a = atual["g"].setdefault(g["modelo"], {"i": 0, "o": 0, "cr": 0, "cw": 0})
                for k in ("i", "o", "cr", "cw"):
                    a[k] += g[k]
                atual["msgs"] += 1
        elif r[0] == "turno":
            atual = None
        elif r[0] in ("skill", "leitura"):
            nome = r[1]
            if r[0] == "leitura":
                chave = (nome, r[2])
                lidos[chave] = lidos.get(chave, 0) + 1
                if r[2] != "SKILL.md":  # referência/script: soma ao custo de carregar da skill em curso
                    if atual and atual["skill"] == nome:
                        atual["carga_bytes"] += r[4]
                    continue
                if atual and atual["skill"] == nome and atual["msgs"] == 0:
                    continue  # SKILL.md lido logo após a chamada da ferramenta: mesma carga
            atual = {"skill": nome, "modo": r[2] if r[0] == "skill" else "leitura", "dia": r[3],
                     "carga_bytes": _tamanho_skill(nome) if r[0] == "skill" else r[4], "g": {}, "msgs": 0}
            skills_ev.append(atual)
    for e in skills_ev:
        out = sum(v["o"] for v in e["g"].values())
        e["carga_tokens"] = int(e.pop("carga_bytes") / BYTES_POR_TOKEN)
        e["abandonada"] = e["msgs"] <= 2 and out < 400
    repetidos = {}
    for (nome, rel), n in lidos.items():
        if n >= 2:
            repetidos[nome] = repetidos.get(nome, 0) + n - 1
    modelos = {}
    for d in dias.values():
        for m, v in d.items():
            modelos[m] = modelos.get(m, 0) + v["o"]
    return {"v": VERSAO_CLAUDE, "baldes": baldes, "sessao": os.path.splitext(os.path.basename(caminho))[0], "subagente": "/subagents/" in caminho,
            "cwd": cwd or "", "projeto": os.path.basename((cwd or "").rstrip("/")) or os.path.basename(os.path.dirname(caminho)),
            "modelo": max(modelos, key=modelos.get) if modelos else "?", "dias": dias, "skills": skills_ev,
            "repetidos": repetidos, "perguntas": perguntas}


# ------------------------------------------------------------------ Codex
RE_USO = re.compile(r'"primary"\s*:\s*\{\s*"used_percent"\s*:\s*([\d.]+)\s*,\s*"window_minutes"\s*:\s*(\d+)\s*,\s*"resets_at"\s*:\s*(\d+)')
RE_MODELO = re.compile(r'"model"\s*:\s*"([^"]+)"')
RE_USER = re.compile(r'"role"\s*:\s*"user"')


def analisar_codex(caminho):
    base_dia = datetime.fromtimestamp(os.path.getmtime(caminho)).strftime("%Y-%m-%d")
    primeiro = ultimo = None
    ultimo_tc, modelo, inicio, cwd = None, None, None, None
    serie, titulo, originator = [], None, ""
    fim_ts, ultima_msg = None, None
    with coletores._abrir(caminho, encoding="utf-8", errors="replace") as f:
        for n, linha in enumerate(f):
            if n == 0:
                m = re.search(r'"timestamp"\s*:\s*"([^"]+)"', linha)
                inicio = m.group(1) if m else None
                m = re.search(r'"cwd"\s*:\s*"([^"]+)"', linha)
                cwd = m.group(1) if m else None
                m = re.search(r'"originator"\s*:\s*"([^"]+)"', linha)
                originator = m.group(1) if m else ""
            if titulo is None and n > 0 and RE_USER.search(linha):
                try:
                    c = json.loads(linha).get("payload", {}).get("content") or []
                    t = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "input_text").strip()
                except (ValueError, AttributeError):
                    t = ""
                if t and not t.startswith("<") and not re.match(r"#?\s*AGENTS\.md instructions", t):   # pula <environment_context> e o AGENTS.md injetado
                    titulo = ocultar(t.splitlines()[0].lstrip("# ").strip(), 70)
            if '"role":"assistant"' in linha.replace('"role": "assistant"', '"role":"assistant"') and '"output_text"' in linha:
                ultima_msg = linha
            tf = re.match(r'\{"timestamp"\s*:\s*"([^"]+)"', linha)
            if tf:
                fim_ts = tf.group(1)
            if modelo is None and '"model"' in linha:
                m = RE_MODELO.search(linha)
                modelo = m.group(1) if m else None
            if '"used_percent"' in linha:
                tm = re.search(r'"timestamp"\s*:\s*"([^"]+)"', linha)
                for m in RE_USO.finditer(linha):
                    v = (float(m.group(1)), int(m.group(3)))
                    if tm and (not serie or serie[-1][1] != v[0] or serie[-1][2] != v[1]) and len(serie) < 400:
                        try:
                            serie.append([datetime.fromisoformat(tm.group(1).replace("Z", "+00:00")).timestamp(), v[0], v[1]])
                        except ValueError:
                            pass
                    if primeiro is None or v[1] != primeiro[1]:
                        primeiro = v          # janela nova: recomeça (vale só a última janela)
                    ultimo = v
            if re.search(r'"type"\s*:\s*"token_count"', linha):
                ultimo_tc = linha
    tok = {"i": 0, "o": 0, "cr": 0, "cw": 0}
    if ultimo_tc:
        try:
            t = json.loads(ultimo_tc)["payload"]["info"]["total_token_usage"]
            cr = int(t.get("cached_input_tokens") or 0)
            tok = {"i": max(0, int(t.get("input_tokens") or 0) - cr), "o": int(t.get("output_tokens") or 0), "cr": cr,
                   "cw": int(t.get("cache_write_input_tokens") or 0)}
        except (ValueError, KeyError, TypeError):
            pass
    delta = round(max(0.0, ultimo[0] - primeiro[0]), 2) if primeiro and ultimo else 0.0
    resultado = ""
    if ultima_msg:
        try:
            c = json.loads(ultima_msg).get("payload", {}).get("content") or []
            resultado = _tres_linhas(" \n".join(x.get("text", "") for x in c if isinstance(x, dict)))
        except (ValueError, AttributeError):
            pass
    try:
        fim_ep = datetime.fromisoformat(fim_ts.replace("Z", "+00:00")).timestamp() if fim_ts else None
        ini_ep = datetime.fromisoformat(inicio.replace("Z", "+00:00")).timestamp() if inicio else None
    except ValueError:
        fim_ep = ini_ep = None
    return {"v": VERSAO_CODEX, "titulo": titulo, "originator": originator, "serie": serie, "janela": primeiro[1] if primeiro else None, "p_ini": primeiro[0] if primeiro else None, "p_fim": ultimo[0] if ultimo else None,
            "sessao": os.path.basename(caminho)[:-6], "cwd": cwd or "", "projeto": os.path.basename((cwd or "").rstrip("/")), "modelo": modelo or "gpt-6-sol",
            "dia": _dia(inicio, base_dia), "inicio": inicio, "inicio_ep": ini_ep, "fim_ep": fim_ep, "resultado": resultado, "pct": delta, "tokens": tok}


# ------------------------------------------------------------------ cache e varredura
def _versao_ok(fonte, dados):
    v = VERSAO_CODEX if fonte == "codex" else VERSAO_CLAUDE
    return '"v": %d' % v in dados[:60]


def _cache(con, caminho):
    return con.execute("SELECT mtime,tam,dados FROM analise_transcript WHERE arquivo=?", (caminho,)).fetchone()


def atualizar(dias=35, maximo=None):
    """Reanalisa só o que mudou. Devolve {'analisados': n, 'total': n}."""
    corte, n, total = time.time() - dias * 86400, 0, 0
    arquivos = [("claude", p) for p in glob.glob(os.path.join(config.claude_home(), "projects", "**", "*.jsonl"), recursive=True)]
    arquivos += [("codex", p) for p in glob.glob(os.path.join(config.codex_home(), "sessions", "*", "*", "*", "rollout-*.jsonl"))]
    with db.conectar() as con:
        for fonte, p in arquivos:
            if coletores._proibido(p):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime < corte:
                continue
            total += 1
            c = _cache(con, p)
            if c and c[0] == st.st_mtime and c[1] == st.st_size and _versao_ok(fonte, c[2]):
                continue
            if maximo is not None and n >= maximo:
                continue
            try:
                d = analisar_claude(p) if fonte == "claude" else analisar_codex(p)
            except OSError:
                continue
            con.execute("INSERT OR REPLACE INTO analise_transcript(arquivo,fonte,mtime,tam,dados) VALUES (?,?,?,?,?)",
                        (p, fonte, st.st_mtime, st.st_size, json.dumps(d, ensure_ascii=False)))
            con.commit()
            n += 1
    return {"analisados": n, "total": total}


def carregar(fonte):
    with db.conectar() as con:
        return [json.loads(r[0]) for r in con.execute("SELECT dados FROM analise_transcript WHERE fonte=?", (fonte,))]


def dados_arquivo(caminho, fonte="codex"):
    """Análise de um arquivo (cacheada por mtime/tamanho; reanalisa se a versão da análise mudou)."""
    st = os.stat(caminho)
    with db.conectar() as con:
        c = _cache(con, caminho)
        if c and c[0] == st.st_mtime and c[1] == st.st_size and _versao_ok(fonte, c[2]):
            return json.loads(c[2])
        d = analisar_claude(caminho) if fonte == "claude" else analisar_codex(caminho)
        con.execute("INSERT OR REPLACE INTO analise_transcript(arquivo,fonte,mtime,tam,dados) VALUES (?,?,?,?,?)",
                    (caminho, fonte, st.st_mtime, st.st_size, json.dumps(d, ensure_ascii=False)))
        return d
