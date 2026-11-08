"""Banco SQLite do quadro comum."""
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

from . import config

ESTADOS = ("nova", "pega", "aguardando", "feita", "bloqueada")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projetos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT UNIQUE NOT NULL, descricao TEXT DEFAULT '', criado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tarefas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, projeto TEXT DEFAULT '', titulo TEXT NOT NULL,
  nivel INTEGER NOT NULL DEFAULT 2 CHECK(nivel BETWEEN 0 AND 3), executor TEXT DEFAULT '', agente TEXT DEFAULT '',
  estado TEXT NOT NULL DEFAULT 'nova' CHECK(estado IN ('nova','pega','aguardando','feita','bloqueada')),
  brief TEXT DEFAULT '', resultado TEXT DEFAULT '', criado REAL NOT NULL, atualizado REAL NOT NULL,
  etapa TEXT DEFAULT '', pausa TEXT DEFAULT '', iniciado REAL, concluido REAL, pid INTEGER, log TEXT DEFAULT '',
  modelo TEXT DEFAULT '', tipo TEXT DEFAULT '', quando_sobrar INTEGER NOT NULL DEFAULT 0,
  voltas INTEGER NOT NULL DEFAULT 0, entrega TEXT DEFAULT '', gate_falhou INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0, cota_pts REAL NOT NULL DEFAULT 0,
  ref TEXT DEFAULT '', sessao_mae TEXT DEFAULT '', auto INTEGER NOT NULL DEFAULT 0, custo_brl REAL, tokens_cache INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mensagens (
  id INTEGER PRIMARY KEY AUTOINCREMENT, de TEXT NOT NULL, para TEXT NOT NULL, texto TEXT NOT NULL,
  lida INTEGER NOT NULL DEFAULT 0, criado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cotas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, agente TEXT NOT NULL, usado_pct REAL NOT NULL, renova_em REAL,
  fonte TEXT DEFAULT 'manual', lido_em REAL NOT NULL, janela_h REAL, confirmado_em REAL);
CREATE INDEX IF NOT EXISTS ix_cotas ON cotas(agente, lido_em);
CREATE TABLE IF NOT EXISTS melhorias (
  id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT NOT NULL, problema TEXT NOT NULL, evidencia TEXT DEFAULT '',
  proposta TEXT DEFAULT '', ganho TEXT DEFAULT '', custo TEXT DEFAULT '', executor TEXT DEFAULT '', risco TEXT DEFAULT '',
  prioridade REAL NOT NULL DEFAULT 0, estado TEXT NOT NULL DEFAULT 'sugerida' CHECK(estado IN ('sugerida','aplicada','aprovada','recusada','feita')),
  tarefa_id INTEGER, origem TEXT DEFAULT 'analise', criado REAL NOT NULL, atualizado REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_melhorias_chave ON melhorias(chave);
CREATE TABLE IF NOT EXISTS analise_transcript (
  arquivo TEXT PRIMARY KEY, fonte TEXT NOT NULL, mtime REAL NOT NULL, tam INTEGER NOT NULL, dados TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS custos_maquina (
  dia TEXT NOT NULL, chave TEXT NOT NULL, tarefa_id INTEGER, cpu_s REAL NOT NULL DEFAULT 0, PRIMARY KEY(dia, chave));
CREATE TABLE IF NOT EXISTS pulsos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, agente TEXT NOT NULL, quando REAL NOT NULL, nota TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, quando REAL NOT NULL, tipo TEXT NOT NULL, texto TEXT DEFAULT '');
"""


def agora():
    return time.time()


def conectar():
    caminho = config.caminho_db()
    if "unittest" in sys.modules and os.path.abspath(caminho) == os.path.abspath(config.banco_de_producao()):
        raise RuntimeError("testes não podem abrir o banco de produção (%s): defina ORQUESTRA_DB para uma pasta temporária" % caminho)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    espera = float(os.environ.get("ORQUESTRA_DB_TIMEOUT", "30"))
    con = sqlite3.connect(caminho, timeout=espera)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=%d" % int(espera * 1000))
    con.executescript(SCHEMA)
    _migrar(con)
    return con


def _ddl(nome):
    m = re.search(r"CREATE TABLE IF NOT EXISTS %s \(.*?\);\n" % nome, SCHEMA, re.S)
    return m.group(0).replace("IF NOT EXISTS ", "")


def _reconstruir(con, nome):
    """Recria a tabela com o DDL atual preservando as linhas (para ampliar CHECKs)."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % nome)]
    con.execute("ALTER TABLE %s RENAME TO %s_velha" % (nome, nome))
    con.execute(_ddl(nome))
    con.execute("INSERT INTO %s(%s) SELECT %s FROM %s_velha" % (nome, ",".join(cols), ",".join(cols), nome))
    con.execute("DROP TABLE %s_velha" % nome)
    con.commit()


def _migrar(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(cotas)")]
    for nome, tipo in (("janela_h", "REAL"), ("confirmado_em", "REAL")):
        if nome not in cols:
            con.execute("ALTER TABLE cotas ADD COLUMN %s %s" % (nome, tipo))
    colt = [r[1] for r in con.execute("PRAGMA table_info(tarefas)")]
    for nome, tipo in (("ref", "TEXT DEFAULT ''"), ("sessao_mae", "TEXT DEFAULT ''"), ("auto", "INTEGER NOT NULL DEFAULT 0"),
                       ("custo_brl", "REAL"), ("tokens_cache", "INTEGER NOT NULL DEFAULT 0")):
        if nome not in colt:
            con.execute("ALTER TABLE tarefas ADD COLUMN %s %s" % (nome, tipo))
    con.execute("CREATE INDEX IF NOT EXISTS ix_tarefas_ref ON tarefas(ref)")
    sql_m = con.execute("SELECT sql FROM sqlite_master WHERE name='melhorias'").fetchone()
    if sql_m and "aplicada" not in sql_m[0]:
        _reconstruir(con, "melhorias")
    """Bancos antigos: amplia o CHECK de estado (reconstrói a tabela) e acrescenta colunas novas."""
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='tarefas'").fetchone()[0]
    if "aguardando" not in sql:
        antigas = [r[1] for r in con.execute("PRAGMA table_info(tarefas)")]
        con.executescript(SCHEMA.split("CREATE TABLE IF NOT EXISTS tarefas (")[1].split("CREATE TABLE")[0].join(
            ["ALTER TABLE tarefas RENAME TO tarefas_velha;\nCREATE TABLE tarefas (", ""]))
        con.execute("INSERT INTO tarefas(%s) SELECT %s FROM tarefas_velha" % (",".join(antigas), ",".join(antigas)))
        con.execute("DROP TABLE tarefas_velha")
        con.commit()


def _linhas(cur):
    return [dict(r) for r in cur.fetchall()]


def parse_instante(txt):
    """Aceita epoch, ISO 8601 ou AAAA-MM-DD. Devolve epoch (UTC se sem fuso)."""
    if txt is None or txt == "":
        return None
    try:
        return float(txt)
    except (TypeError, ValueError):
        pass
    s = str(txt).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError("data inválida: %r (use ISO 8601 ou epoch)" % txt)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def evento(con, tipo, texto=""):
    con.execute("INSERT INTO eventos(quando,tipo,texto) VALUES (?,?,?)", (agora(), tipo, texto))


# ---------- projetos
def projeto_novo(nome, descricao=""):
    with conectar() as con:
        con.execute("INSERT OR IGNORE INTO projetos(nome,descricao,criado) VALUES (?,?,?)", (nome, descricao, agora()))
        evento(con, "projeto", "projeto %s" % nome)


def projetos():
    with conectar() as con:
        ps = _linhas(con.execute("SELECT * FROM projetos ORDER BY nome"))
        for p in ps:
            c = _linhas(con.execute(
                "SELECT estado, COUNT(*) n FROM tarefas WHERE projeto=? GROUP BY estado", (p["nome"],)))
            p["tarefas"] = {r["estado"]: r["n"] for r in c}
        return ps


# ---------- tarefas
def tarefa_nova(titulo, nivel=2, projeto="", executor="", brief="", tipo="", quando_sobrar=False):
    if not titulo.strip():
        raise ValueError("título vazio")
    nivel = int(nivel)
    if nivel not in (0, 1, 2, 3):
        raise ValueError("nível deve ser 0–3")
    t = agora()
    with conectar() as con:
        if projeto:
            con.execute("INSERT OR IGNORE INTO projetos(nome,criado) VALUES (?,?)", (projeto, t))
        cur = con.execute(
            "INSERT INTO tarefas(projeto,titulo,nivel,executor,brief,criado,atualizado,tipo,quando_sobrar) VALUES (?,?,?,?,?,?,?,?,?)",
            (projeto, titulo.strip(), nivel, executor, brief, t, t, tipo or _tipo(titulo), 1 if quando_sobrar else 0))
        evento(con, "tarefa", "nova #%d %s" % (cur.lastrowid, titulo.strip()))
        return cur.lastrowid


def _mudar(tid, estado, agente=None, resultado=None, motivo=None):
    t = agora()
    with conectar() as con:
        r = con.execute("SELECT * FROM tarefas WHERE id=?", (tid,)).fetchone()
        if not r:
            raise KeyError("tarefa %s não existe" % tid)
        if estado == "pega" and r["estado"] in ("pega", "feita") and r["agente"] and agente != r["agente"]:
            raise ValueError("tarefa %s já está %s com %s" % (tid, r["estado"], r["agente"]))
        campos = {"estado": estado, "atualizado": t}
        if estado == "pega" and not r["iniciado"]:
            campos["iniciado"] = t
        if estado in ("feita", "aguardando", "bloqueada"):
            campos["concluido"] = t
        if agente:
            campos["agente"] = agente
        if resultado is not None:
            campos["resultado"] = resultado
        if motivo is not None:
            campos["resultado"] = "BLOQUEADA: " + motivo
        sql = "UPDATE tarefas SET " + ",".join("%s=?" % k for k in campos) + " WHERE id=?"
        con.execute(sql, list(campos.values()) + [tid])
        evento(con, "tarefa", "#%d → %s%s" % (tid, estado, " (%s)" % agente if agente else ""))


def _tipo(titulo):
    from . import cerebro
    return cerebro.tipo_de(titulo)


def tarefa_pegar(tid, agente, forcar=False):
    if not agente:
        raise ValueError("diga quem pegou (--agente)")
    if not forcar:
        from . import orcamento
        motivo = orcamento.em_espera(tarefa(tid))
        if motivo:
            raise ValueError("tarefa %s em espera: %s (use --forcar se for urgente)" % (tid, motivo))
    _mudar(int(tid), "pega", agente=agente)


def tarefa_feita(tid, resultado="", agente=None, tokens_in=0, tokens_out=0, modelo=None):
    """Nível 1–2 vai para 'aguardando' revisão; só `tarefa_revisada` a deixa 'feita'. Níveis 0 e 3 fecham direto."""
    t = tarefa(tid)
    estado = "aguardando" if t["nivel"] in (1, 2) else "feita"
    _mudar(int(tid), estado, agente=agente, resultado=resultado)
    with conectar() as con:
        con.execute("UPDATE tarefas SET tokens_in=tokens_in+?, tokens_out=tokens_out+?, modelo=COALESCE(?,modelo) WHERE id=?",
                    (int(tokens_in or 0), int(tokens_out or 0), modelo, int(tid)))
        if estado == "feita":
            con.execute("UPDATE tarefas SET entrega=? WHERE id=?", (_entrega(tarefa(tid)), int(tid)))
    return estado


def _entrega(t):
    return "quebrou" if t["gate_falhou"] else "voltou" if t["voltas"] else "primeira"


def tarefa_revisada(tid, por="orquestrador"):
    t = tarefa(tid)
    if t["estado"] != "aguardando":
        raise ValueError("tarefa %s não está aguardando revisão (estado: %s)" % (tid, t["estado"]))
    with conectar() as con:
        con.execute("UPDATE tarefas SET estado='feita', entrega=?, atualizado=? WHERE id=?", (_entrega(t), agora(), int(tid)))
        evento(con, "tarefa", "#%s revisada por %s → feita (%s)" % (tid, por, _entrega(t)))


def tarefa_voltou(tid, motivo):
    """Revisão devolve a tarefa ao executor."""
    t = tarefa(tid)
    if t["estado"] not in ("aguardando", "feita"):
        raise ValueError("só volta tarefa aguardando revisão ou feita")
    with conectar() as con:
        con.execute("UPDATE tarefas SET estado='pega', voltas=voltas+1, resultado=?, concluido=NULL, atualizado=? WHERE id=?",
                    ("VOLTOU DA REVISÃO: " + motivo, agora(), int(tid)))
        evento(con, "tarefa", "#%s voltou da revisão: %s" % (tid, motivo))


def tarefa_gate(tid, falhou=True):
    with conectar() as con:
        con.execute("UPDATE tarefas SET gate_falhou=gate_falhou+? , atualizado=? WHERE id=?", (1 if falhou else 0, agora(), int(tid)))
        evento(con, "gate", "#%s gate %s" % (tid, "FALHOU" if falhou else "passou"))


def placar(agrupar=("nivel", "modelo", "tipo")):
    """Entrega por executor: de primeira / voltou da revisão / quebrou o gate."""
    cols = ",".join(agrupar)
    with conectar() as con:
        rs = _linhas(con.execute(
            "SELECT %s, COUNT(*) total, SUM(entrega='primeira') primeira, SUM(entrega='voltou') voltou, SUM(entrega='quebrou') quebrou "
            "FROM tarefas WHERE estado='feita' AND entrega<>'' GROUP BY %s ORDER BY total DESC" % (cols, cols)))
    for r in rs:
        r["taxa_primeira"] = round((r["primeira"] or 0) / r["total"], 2) if r["total"] else None
    return rs


def placar_executores(desde=None):
    """Tarefas automáticas dos executores (subagentes e Codex) por modelo: quantas, quantas fecharam, tokens e custo equivalente."""
    q = ("SELECT modelo, executor, COUNT(*) total, SUM(estado='feita') feitas, SUM(estado='bloqueada') bloqueadas, SUM(estado='pega') rodando, "
         "SUM(tokens_in+tokens_out+tokens_cache) tokens, ROUND(SUM(COALESCE(custo_brl,0)),2) brl FROM tarefas WHERE auto=1")
    a = []
    if desde:
        q += " AND iniciado>=?"
        a.append(desde)
    with conectar() as con:
        return _linhas(con.execute(q + " GROUP BY modelo, executor ORDER BY brl DESC", a))


def cota_consumo(agente, ini, fim):
    """Pontos de cota consumidos entre dois instantes (leituras gravadas)."""
    with conectar() as con:
        def em(t):
            r = con.execute("SELECT usado_pct FROM cotas WHERE agente=? AND lido_em<=? ORDER BY id DESC LIMIT 1", (agente, t)).fetchone()
            if not r:
                r = con.execute("SELECT usado_pct FROM cotas WHERE agente=? ORDER BY id LIMIT 1", (agente,)).fetchone()
            return r[0] if r else None
        a, b = em(ini), em(fim)
    return round(max(0.0, b - a), 1) if a is not None and b is not None else None


def tarefa_bloqueada(tid, motivo, agente=None):
    _mudar(int(tid), "bloqueada", agente=agente, motivo=motivo)


def tarefa_atualizar(tid, **campos):
    ok = {k: v for k, v in campos.items() if k in ("titulo", "nivel", "executor", "brief", "projeto")}
    if not ok:
        return
    ok["atualizado"] = agora()
    with conectar() as con:
        con.execute("UPDATE tarefas SET " + ",".join("%s=?" % k for k in ok) + " WHERE id=?", list(ok.values()) + [int(tid)])


def tarefas(estado=None, projeto=None):
    q, a = "SELECT * FROM tarefas WHERE 1=1", []
    if estado:
        q += " AND estado=?"; a.append(estado)
    if projeto:
        q += " AND projeto=?"; a.append(projeto)
    with conectar() as con:
        return _linhas(con.execute(q + " ORDER BY atualizado DESC", a))


# ---------- mensagens
def msg(de, para, texto):
    if not texto.strip():
        raise ValueError("mensagem vazia")
    with conectar() as con:
        cur = con.execute("INSERT INTO mensagens(de,para,texto,criado) VALUES (?,?,?,?)", (de, para, texto, agora()))
        return cur.lastrowid


def msgs(agente=None, nao_lidas=False, marcar=False, limite=200):
    q, a = "SELECT * FROM mensagens WHERE 1=1", []
    if agente:
        q += " AND (para=? OR para='todos')"; a.append(agente)
    if nao_lidas:
        q += " AND lida=0"
    with conectar() as con:
        r = _linhas(con.execute(q + " ORDER BY id DESC LIMIT ?", a + [limite]))
        if marcar and r:
            con.execute("UPDATE mensagens SET lida=1 WHERE id IN (%s)" % ",".join(str(x["id"]) for x in r))
        return r


def msgs_lidas():
    """Marca todas as mensagens como lidas (botão do painel). Devolve quantas eram não lidas."""
    with conectar() as con:
        return con.execute("UPDATE mensagens SET lida=1 WHERE lida=0").rowcount


# ---------- cotas
STALE_S = 1800   # leitura sem atualização há mais de 30 min = desatualizada


def janela_do_nome(agente):
    """Duração da janela em horas pelo nome (`claude-5h` → 5; semanal/codex → 168)."""
    m = re.search(r"(\d+)\s*h\b", agente)
    return float(m.group(1)) if m else 168.0


def cota_registrar(agente, usado, renova=None, fonte="manual", janela_h=None, quando=None):
    usado = float(usado)
    if not 0 <= usado <= 100:
        raise ValueError("usado deve estar entre 0 e 100")
    renova = parse_instante(renova)
    t = quando or agora()          # `quando`: hora real da leitura (ex.: a do rollout do Codex), não a da coleta
    janela_h = janela_h or janela_do_nome(agente)
    with conectar() as con:
        ult = con.execute("SELECT * FROM cotas WHERE agente=? ORDER BY id DESC LIMIT 1", (agente,)).fetchone()
        # valor e janela iguais: só confirma (histórico leve), não grava linha nova
        if ult and ult["usado_pct"] == usado and ult["renova_em"] == renova:
            con.execute("UPDATE cotas SET confirmado_em=?, fonte=?, janela_h=? WHERE id=?", (t, fonte, janela_h, ult["id"]))
            return
        con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em,janela_h,confirmado_em) VALUES (?,?,?,?,?,?,?)",
                    (agente, usado, renova, fonte, t, janela_h, t))


def ultima_renovacao(agente):
    with conectar() as con:
        r = con.execute("SELECT renova_em FROM cotas WHERE agente=? ORDER BY id DESC LIMIT 1", (agente,)).fetchone()
    return r[0] if r else None


def _ritmo_atual(con, ag, u, t, janela_h):
    """Pontos consumidos por hora (curta) ou por dia (semanal) pelas leituras do ciclo atual; None sem base."""
    span_h = 1.0 if janela_h <= 24 else 24.0
    ciclo = "ABS(COALESCE(renova_em,0)-?)<120"
    ref = con.execute("SELECT usado_pct, lido_em FROM cotas WHERE agente=? AND %s AND lido_em<=? ORDER BY id DESC LIMIT 1" % ciclo,
                      (ag, u["renova_em"] or 0, t - span_h * 3600)).fetchone()
    if not ref:
        ref = con.execute("SELECT usado_pct, lido_em FROM cotas WHERE agente=? AND %s ORDER BY id LIMIT 1" % ciclo, (ag, u["renova_em"] or 0)).fetchone()
    fim_t = u["confirmado_em"] or u["lido_em"]
    elapsed = (fim_t - ref["lido_em"]) if ref else 0
    if not ref or elapsed < 600:
        return 0.0
    por_h = max(0.0, u["usado_pct"] - ref["usado_pct"]) / (elapsed / 3600.0)
    return por_h if janela_h <= 24 else por_h * 24.0


LIMITE_LEITURA_S = {"curta": 3600, "semanal": 21600}   # leitura mais velha que isto ganha a etiqueta cinza "lido há …"


def _leituras_do_ciclo(con, ag, u):
    rows = con.execute("SELECT usado_pct, lido_em, confirmado_em, id FROM cotas WHERE agente=? AND ABS(COALESCE(renova_em,0)-?)<120 ORDER BY id",
                       (ag, u["renova_em"] or 0)).fetchall()
    out = [(r["lido_em"], r["usado_pct"]) for r in rows[:-1]]
    if rows:
        out.append((rows[-1]["confirmado_em"] or rows[-1]["lido_em"], rows[-1]["usado_pct"]))
    return out


def cotas(agora_=None):
    """Última leitura de cada IA/janela: confirmado × estimado, disponível, renova, ritmo atual × permitido, previsão de estouro."""
    t = agora_ or agora()
    saida = []
    with conectar() as con:
        for (ag,) in con.execute("SELECT DISTINCT agente FROM cotas ORDER BY agente").fetchall():
            u = con.execute("SELECT * FROM cotas WHERE agente=? ORDER BY id DESC LIMIT 1", (ag,)).fetchone()
            d = dict(u)
            jh = d.get("janela_h") or janela_do_nome(ag)
            d["janela_h"] = jh
            curta = jh <= 24
            d["confirmado"] = d["confirmado_em"] or d["lido_em"]
            d["confirmado_pct"] = d["usado_pct"]
            d["origem"], d["resetou"], d["fator"] = "confirmado", False, None
            ritmo_est = None
            est = None
            if ag.startswith("claude-"):                 # janelas do plano Claude: estimativa calibrada pelos transcripts
                try:
                    from . import estimativa
                    est = estimativa.estimar(ag, _leituras_do_ciclo(con, ag, u), d["renova_em"], jh, t)
                except Exception:
                    est = None
            if est:
                d.update(usado_pct=est["estimado_pct"], renova_em=est["renova_efetiva"], origem=est["origem"], resetou=est["resetou"], fator=est["fator"])
                ritmo_est = est["ritmo"]
            elif d["renova_em"] and d["renova_em"] <= t:   # sem estimativa (ex.: Codex): a janela renovou, zera sozinha
                per = jh * 3600.0
                d["renova_em"] += (int((t - d["renova_em"]) // per) + 1) * per
                d.update(usado_pct=0.0, origem="zerou na renovação", resetou=True)
            ini_dia = datetime.fromtimestamp(t).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            base = con.execute("SELECT usado_pct FROM cotas WHERE agente=? AND lido_em<? ORDER BY id DESC LIMIT 1", (ag, ini_dia)).fetchone()
            if not base:
                base = con.execute("SELECT usado_pct FROM cotas WHERE agente=? AND lido_em>=? ORDER BY id LIMIT 1", (ag, ini_dia)).fetchone()
            d["consumo_hoje"] = max(0.0, d["usado_pct"] - base["usado_pct"]) if base else 0.0
            d.update(ritmo(d["usado_pct"], d["renova_em"], t))
            d["restante"] = round(100 - d["usado_pct"], 1)
            d["disponivel_pct"] = d["restante"]
            horas = (d["renova_em"] - t) / 3600.0 if d["renova_em"] and d["renova_em"] > t else None
            d["horas_ate_renovar"] = round(horas, 2) if horas is not None else None
            d["ritmo_unidade"] = "pts/h" if curta else "pts/dia"
            if ritmo_est is not None:
                at = ritmo_est
            elif d["resetou"]:
                at = 0.0
            else:
                at = _ritmo_atual(con, ag, d, t, jh)
                if ag == "codex" and not curta:
                    ext = _ritmo_externo()
                    at = ext if ext is not None else at
            d["ritmo_atual"] = round(at, 1)
            if horas:
                d["ritmo_permitido"] = round(d["restante"] / (horas if curta else horas / 24.0), 1)
                d["previsao_fim_pct"] = round(d["usado_pct"] + at * (horas if curta else horas / 24.0))
            else:
                d["ritmo_permitido"] = d["previsao_fim_pct"] = None
            d["estoura"] = bool(d["previsao_fim_pct"] is not None and d["previsao_fim_pct"] > 100)
            d["acima_ritmo"] = d["estoura"]          # o alerta só dispara pela previsão contra a janela
            d["idade_leitura_s"] = int(t - d["confirmado"])
            # leitura velha é só uma etiqueta cinza; o que é estimado nunca fica "desatualizado"
            d["leitura_velha"] = d["idade_leitura_s"] > LIMITE_LEITURA_S["curta" if curta else "semanal"]
            d["desatualizada"] = bool(d["leitura_velha"] and d["origem"] == "confirmado")
            d["parado_sem_consumo"] = bool(ag == "codex" and d["idade_leitura_s"] > 1800)
            d["texto_ritmo"] = "ritmo %s %s (permitido %s), previsão %s%% ao renovar" % (
                d["ritmo_atual"], d["ritmo_unidade"], d["ritmo_permitido"], d["previsao_fim_pct"])
            saida.append(d)
    return saida


def _ritmo_externo():
    try:
        from . import custos
        return custos.ritmo_codex_raw()
    except Exception:
        return None


def ritmo(usado, renova_em, t=None):
    """Ritmo diário = (100 − usado) ÷ dias até renovar."""
    t = t or agora()
    if not renova_em or renova_em <= t:
        return {"dias_ate_renovar": None, "ritmo_dia": None}
    dias = max((renova_em - t) / 86400.0, 0.25)
    return {"dias_ate_renovar": round(dias, 2), "ritmo_dia": round((100 - usado) / dias, 1)}


# ---------- pulso
def pulso(agente, nota=""):
    with conectar() as con:
        con.execute("INSERT INTO pulsos(agente,quando,nota) VALUES (?,?,?)", (agente, agora(), nota))


def pulsos_recentes(janela=900):
    with conectar() as con:
        return _linhas(con.execute(
            "SELECT agente, MAX(quando) quando, nota FROM pulsos WHERE quando>? GROUP BY agente",
            (agora() - janela,)))


def eventos(limite=50):
    with conectar() as con:
        return _linhas(con.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (limite,)))


# ---------- ao vivo: etapa, pausa, vínculo com processo, duração média
def tarefa_etapa(tid, texto):
    with conectar() as con:
        if not con.execute("SELECT 1 FROM tarefas WHERE id=?", (int(tid),)).fetchone():
            raise KeyError("tarefa %s não existe" % tid)
        con.execute("UPDATE tarefas SET etapa=?, atualizado=? WHERE id=?", (texto, agora(), int(tid)))


def tarefa_pausar(tid, motivo):
    if not motivo.strip():
        raise ValueError("diga o motivo da pausa")
    with conectar() as con:
        if not con.execute("SELECT 1 FROM tarefas WHERE id=?", (int(tid),)).fetchone():
            raise KeyError("tarefa %s não existe" % tid)
        con.execute("UPDATE tarefas SET pausa=?, atualizado=? WHERE id=?", (motivo, agora(), int(tid)))
        evento(con, "pausa", "#%s pausada: %s" % (tid, motivo))


def tarefa_retomar(tid):
    with conectar() as con:
        con.execute("UPDATE tarefas SET pausa='', atualizado=? WHERE id=?", (agora(), int(tid)))
        evento(con, "pausa", "#%s retomada" % tid)


def tarefa_vincular(tid, pid=None, log=None, modelo=None):
    with conectar() as con:
        con.execute("UPDATE tarefas SET pid=COALESCE(?,pid), log=COALESCE(?,log), modelo=COALESCE(?,modelo) WHERE id=?",
                    (pid, log, modelo, int(tid)))


def tarefa(tid):
    with conectar() as con:
        r = con.execute("SELECT * FROM tarefas WHERE id=?", (int(tid),)).fetchone()
        if not r:
            raise KeyError("tarefa %s não existe" % tid)
        return dict(r)


def duracao_media(nivel, tipo=""):
    """Média (s) das tarefas feitas do mesmo nível (e tipo, se houver ≥2 do tipo). None sem histórico."""
    with conectar() as con:
        def med(q, a):
            v = [r[0] for r in con.execute(q, a) if r[0] and r[0] > 0]
            return (sum(v) / len(v), len(v)) if v else (None, 0)
        if tipo:
            m, n = med("SELECT concluido-iniciado FROM tarefas WHERE estado IN ('feita','aguardando') AND nivel=? AND tipo=? AND iniciado IS NOT NULL AND concluido IS NOT NULL", (nivel, tipo))
            if n >= 2:
                return m
        m, n = med("SELECT concluido-iniciado FROM tarefas WHERE estado IN ('feita','aguardando') AND nivel=? AND iniciado IS NOT NULL AND concluido IS NOT NULL", (nivel,))
        return m
