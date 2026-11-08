"""O cérebro: regras da hierarquia (skill orquestrar) → nível, executor e modelo sugeridos."""
import re
import unicodedata

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"


def _norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# palavras-chave (sem acento)
DINHEIRO = ["preco", "ads", "anuncio pago", "dinheiro", "reembolso", "custo", "margem", "orcamento", "desconto", "promocao"]
DECISAO = ["decidir", "decisao", "escolher", "estrategia", "aprovar", "arquitetura", "desenhar", "desenho", "credencial",
           "producao", "publicar", "merge", "deploy", "revisar diff", "priorizar", "triagem"]
NAVEGADOR = ["navegador", "logado", "sessao logada", "computer-use", "computer use", "chrome", "painel do marketplace"]
CODIGO = ["consertar", "conserto", "corrigir", "bug", "codigo", "implementar", "refator", "teste", "tela", "funcao",
          "endpoint", "rota", "migracao", "criar app", "escrever", "feature", "pr "]
LEITURA = ["varrer", "ler ", "leitura", "inventario", "listar", "resumir", "conferir", "logs", "log ", "relatar",
           "contar", "mapear", "levantar", "checar", "verificar", "rodar testes", "buscar", "procurar"]
SCRIPT = ["rodar comando", "um comando", "script", "git status", "ls ", "contar linhas", "cron", "vigia"]


def _tem(txt, palavras):
    return [p for p in palavras if p in txt]


def tipo_de(descricao):
    t = _norm(descricao) + " "
    for nome, pal in (("decisao", DINHEIRO + DECISAO), ("navegador", NAVEGADOR), ("codigo", CODIGO), ("script", SCRIPT), ("leitura", LEITURA)):
        if _tem(t, pal):
            return nome
    return "geral"


def _taxa(placar, nivel, tipo=None):
    tot = pri = 0
    for r in placar or []:
        if r.get("nivel") == nivel and (tipo is None or r.get("tipo") == tipo):
            tot += r["total"]; pri += r["primeira"] or 0
    return (pri / tot, tot) if tot else (None, 0)


def recomendar(descricao, cotas=None, placar=None):
    """Devolve dict: nivel, executor, modelo, dono (bool), desceu (bool), porque (uma linha).

    `cotas`: lista de dicts como db.cotas() (agente, restante, acima_ritmo, ritmo_dia).
    """
    t = _norm(descricao) + " "
    cotas = cotas or []
    acima = [c for c in cotas if c.get("acima_ritmo")]
    codex = next((c for c in cotas if c["agente"] == "codex"), None)
    codex_baixo = bool(codex and codex.get("restante") is not None and codex["restante"] < 40)

    din, dec, nav = _tem(t, DINHEIRO), _tem(t, DECISAO), _tem(t, NAVEGADOR)
    cod, lei, scr = _tem(t, CODIGO), _tem(t, LEITURA), _tem(t, SCRIPT)

    dono = bool(din or any(w in t for w in ("decidir", "decisao", "aprovar", "escolher")))
    r = {"nivel": 2, "executor": "Claude Sonnet", "modelo": SONNET, "dono": dono, "desceu": False}

    if din or dec:
        motivo = din or dec
        r.update(nivel=3, executor="orquestrador", modelo=OPUS)
        r["porque"] = "Nível 3: mexe com %s — decisão/dinheiro/produção fica com o orquestrador%s; execução braçal desce um nível." % (
            "/".join(motivo[:2]), " e o dono" if dono else "")
    elif nav:
        r.update(nivel=2, executor="Codex com navegador (ou Claude in Chrome)", modelo="gpt-6-sol")
        r["porque"] = "Exige sessão logada (%s): só Codex com computer-use ou Claude in Chrome fazem; a API não resolve." % nav[0]
    elif cod:
        r.update(nivel=2, executor="Claude Sonnet", modelo=SONNET)
        r["porque"] = "Nível 2: código/conserto com brief claro (%s) vai para Sonnet, com teste e revisão do orquestrador." % cod[0]
    elif scr and not lei:
        r.update(nivel=0, executor="script/comando direto", modelo="sem modelo")
        r["porque"] = "Nível 0: checagem mecânica (%s) — um comando resolve, sem gastar modelo." % scr[0]
    elif lei or scr:
        r.update(nivel=1, executor="Claude Haiku", modelo=HAIKU)
        r["porque"] = "Nível 1: só leitura/varredura (%s) — Haiku faz bem e custa menos." % (lei or scr)[0].strip()
    else:
        r["porque"] = "Nível 2 por padrão: não achei sinal de leitura simples nem de decisão; sem brief claro, escreva o brief antes de lançar."

    # placar: executor que mal entrega de primeira sobe um nível; o que entrega bem é confirmado
    if r["nivel"] in (1, 2):
        taxa, n = _taxa(placar, r["nivel"], tipo_de(descricao))
        if taxa is None or n < 3:
            taxa, n = _taxa(placar, r["nivel"])
        if taxa is not None and n >= 3:
            if taxa < 0.5:
                antes = r["nivel"]
                r["nivel"] = antes + 1
                r["porque"] += " Placar: nível %d entregou de primeira só %.0f%% (n=%d) — subi para o nível %d." % (antes, taxa * 100, n, r["nivel"])
                if r["nivel"] == 2:
                    r.update(executor="Claude Sonnet", modelo=SONNET)
                else:
                    r.update(executor="orquestrador", modelo=OPUS)
                r["placar"] = "subiu"
            elif taxa >= 0.8:
                r["porque"] += " Placar confirma: %.0f%% de primeira (n=%d)." % (taxa * 100, n)
                r["placar"] = "ok"

    # Codex como alternativa barata quando a cota dele está folgada
    if r["nivel"] in (1, 2) and not nav and codex and not codex_baixo and not codex.get("acima_ritmo"):
        r["alternativa"] = "Codex gpt-6-%s" % ("luna" if r["nivel"] == 1 else "sol")
    elif r["nivel"] in (1, 2) and codex_baixo:
        r["porque"] += " Codex abaixo de 40%% de sobra: no máximo 1 por vez, tarefa pequena."

    # ritmo de cota: passou do ritmo → desce nível
    if acima and r["nivel"] >= 1:
        nomes = ", ".join("%s (%s)" % (c["agente"], c.get("texto_ritmo", "acima do ritmo")) for c in acima)
        if r["nivel"] == 3:
            r["porque"] += " Cota acima do ritmo (%s): delegue todo o braçal e gaste só em decisão." % nomes
        else:
            antes = r["nivel"]
            r["nivel"] = antes - 1
            r["desceu"] = True
            if r["nivel"] == 1:
                r.update(executor="Claude Haiku", modelo=HAIKU)
            else:
                r.update(executor="script/comando direto", modelo="sem modelo")
            r["porque"] += " Cota acima do ritmo (%s): desci do nível %d para %d — só leitura/pedaços pequenos; adie o resto." % (
                nomes, antes, r["nivel"])
            r.pop("alternativa", None)
    return r
