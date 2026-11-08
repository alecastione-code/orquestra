"""Uso estimado das janelas do plano Claude entre duas leituras reais.

estimado = última leitura real + fator × Σ(peso do modelo × tokens ponderados desde a leitura)
O fator (pontos por token ponderado) vem de leituras reais consecutivas (Δ% ÷ Δ tokens ponderados) e é recalibrado a cada leitura nova.
Quando resetsAt passa, a janela zera sozinha e recomeça a contar. Nada de rede, credencial ou modelo: só os transcripts já lidos."""
import bisect
import json
import time

from . import custo, db, transcritos

PADRAO = {"modelos": {"fable": 5.0, "opus": 3.0, "sonnet": 1.0, "haiku": 0.3},
          "tokens": {"entrada": 1.0, "saida": 5.0, "cache_escrita": 1.25, "cache_leitura": 0.01}}
_idx = {"k": None, "ts": [], "linhas": []}


def pesos():
    return custo.carregar_config("pesos.json", PADRAO)


def peso_modelo(modelo, p=None):
    p = p or pesos()
    m = (modelo or "").lower()
    return next((v for k, v in p["modelos"].items() if k in m), 1.0)


def _chave_indice():
    with db.conectar() as con:
        r = con.execute("SELECT COUNT(*), COALESCE(MAX(mtime),0) FROM analise_transcript WHERE fonte='claude'").fetchone()
    return (r[0], r[1], json.dumps(pesos(), sort_keys=True))


def _indice():
    """Lista ordenada de (início do balde de 1 min, modelo, tokens ponderados sem o peso do modelo)."""
    k = _chave_indice()
    if _idx["k"] == k:
        return _idx["ts"], _idx["linhas"]
    p, tw = pesos(), pesos()["tokens"]
    linhas = []
    for d in transcritos.carregar("claude"):
        for chave, (i, o, cr, cw) in (d.get("baldes") or {}).items():
            ts, modelo = chave.split("|", 1)
            linhas.append((int(ts), modelo, i * tw["entrada"] + o * tw["saida"] + cw * tw["cache_escrita"] + cr * tw["cache_leitura"]))
    linhas.sort()
    _idx.update(k=k, ts=[x[0] for x in linhas], linhas=linhas)
    return _idx["ts"], linhas


def tokens_ponderados(t_ini, t_fim, filtro=None):
    """Σ peso_do_modelo × tokens ponderados com o balde de 1 min (ponto médio) em [t_ini, t_fim). `filtro`: só modelos cujo id contém o texto."""
    ts, linhas = _indice()
    p = pesos()
    a, b = bisect.bisect_left(ts, int(t_ini) - 30), bisect.bisect_left(ts, int(t_fim) - 30)
    total = 0.0
    for _, modelo, w in linhas[a:b]:
        if filtro and filtro not in modelo.lower():
            continue
        total += peso_modelo(modelo, p) * w
    return total


def filtro_da_janela(agente):
    """`claude-semanal-fable` usa só tokens do Fable; `claude-5h` e `claude-semanal` usam todos os modelos."""
    return agente[len("claude-semanal-"):] if agente.startswith("claude-semanal-") else None


def fator(leituras, inicio_ciclo, filtro):
    """Pontos por token ponderado. `leituras`: [(t, usado%)] do ciclo, em ordem. Duas leituras seguidas calibram; uma só serve de base."""
    pares = []
    for (ta, ua), (tb, ub) in zip(leituras, leituras[1:]):
        w = tokens_ponderados(ta, tb, filtro)
        if ub > ua and w > 0:
            pares.append((ub - ua, w))
    if pares:                        # as 3 últimas calibragens, ponderadas pelo volume
        pares = pares[-3:]
        return sum(p for p, _ in pares) / sum(w for _, w in pares)
    t0, u0 = leituras[-1]
    w = tokens_ponderados(inicio_ciclo, t0, filtro)
    return u0 / w if u0 > 0 and w > 0 else None


def estimar(agente, leituras, renova_em, janela_h, agora=None):
    """Devolve dict com o uso estimado agora, ou None sem leitura. `leituras`: [(t, usado%)] do ciclo atual (última = a mais recente)."""
    agora = agora or time.time()
    if not leituras:
        return None
    periodo = janela_h * 3600.0
    filtro = filtro_da_janela(agente)
    t0, u0 = leituras[-1]
    inicio = (renova_em - periodo) if renova_em else leituras[0][0]
    f = fator(leituras, inicio, filtro)
    resetou, base, ref_t, renova = False, u0, t0, renova_em
    if renova_em and agora >= renova_em:             # a janela renovou: zera sozinha e recomeça a contar
        k = int((agora - renova_em) // periodo) + 1
        renova = renova_em + k * periodo
        ref_t, base, resetou = renova - periodo, 0.0, True
    w = tokens_ponderados(ref_t, agora, filtro)
    est = min(100.0, base + (f * w if f else 0.0))
    # ritmo: pts/h na janela curta (última hora), pts/dia na semanal (últimas 24 h). Janela jovem extrapola só o que já passou,
    # com um piso (15 min na janela de 5 h; 1 dia na semanal) para uma rajada no começo da janela não virar um "ritmo do dia" que assusta à toa.
    span = 3600.0 if janela_h <= 24 else 86400.0
    inicio_ef = renova - periodo if renova else ref_t
    decorrido = max(min(span, agora - inicio_ef), 900.0 if janela_h <= 24 else 86400.0)
    ritmo = (f or 0.0) * tokens_ponderados(max(agora - span, inicio_ef), agora, filtro) * (span / decorrido)
    return {"estimado_pct": round(est, 1), "base_pct": u0, "confirmado_t": t0, "fator": f, "tokens_ponderados": w, "resetou": resetou,
            "renova_efetiva": renova, "ritmo": round(ritmo, 2) if f else None,   # sem fator, o ritmo vem das leituras reais
            "origem": "estimado" if (resetou or est - u0 >= 0.5) else "confirmado"}
