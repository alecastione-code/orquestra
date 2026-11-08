"""Template de brief da skill orquestrar + diário do projeto."""
import os
import re
from datetime import datetime

from . import config, db

SECOES = ["Objetivo", "Skills obrigatórias", "Arquivos e área", "Regras invioláveis", "Critério de pronto", "Testes a rodar", "Onde escrever o resultado"]


def esforco(nivel, mecanico=False):
    """Esforço de raciocínio sugerido pela hierarquia (esforço também é custo)."""
    return "low" if mecanico or nivel <= 1 else "padrão" if nivel == 2 else "alto"


def novo(titulo, nivel=2, projeto="", mecanico=False, tipo=""):
    mecanico = mecanico or nivel <= 1 or tipo in ("leitura", "script")
    ponytail = "ponytail (ultra — trabalho mecânico)" if mecanico else "ponytail (full)"
    rev = "O orquestrador revisa o diff antes de qualquer merge ou ação externa." if nivel >= 2 else "Entregue o relatório; o orquestrador confere."
    return f"""# [ID] — {titulo}

Nível {nivel} · projeto: {projeto or '[PREENCHER]'} · criado em {datetime.now():%d/%m/%Y %H:%M}

## Objetivo
[PREENCHER: uma frase — o que muda no mundo quando isso estiver pronto]

## Skills obrigatórias
- Use: {ponytail}, karpathy-guidelines (mudança cirúrgica, suposições declaradas, critério verificável) e ponytail-review antes de abrir o PR.
- Esforço sugerido: **{esforco(nivel, mecanico)}** (nível {nivel}: {'mecânico, esforço baixo' if mecanico else 'esforço padrão' if nivel == 2 else 'desenho/revisão de risco, esforço alto'}).

## Arquivos e área
[PREENCHER: pastas/arquivos que pode mexer e os que NÃO pode; branch/worktree próprio]

## Regras invioláveis
- Produção só leitura; nunca imprimir tokens, senhas ou dados de comprador; sem merge. {rev}
- Nunca dois executores escrevendo os mesmos arquivos.
- [PREENCHER: regras específicas do projeto]

## Critério de pronto
[PREENCHER: verificável — comando que passa, tela que mostra X, número que bate]

## Testes a rodar
- [PREENCHER: suíte do projeto]
- Todo teste que cita o que for mexido: `git grep -l "<id/carimbo/rota mexida>" -- '*test*'` e rodar os que aparecerem.

## Onde escrever o resultado
- Resultado em 3 linhas: `orq tarefa feita <id> --resultado "…"` (nível 1–2 vai para revisão).
- Se travar ou faltar cota: `orq tarefa bloqueada <id> --motivo "…"`, nunca abandonar calado.
- Etapas longas: `orq tarefa etapa <id> "…"` e `orq pulso`.
"""


def validar(texto):
    """Lista o que falta no brief: seções ausentes e campos [PREENCHER] sobrando."""
    faltam = [s for s in SECOES if not re.search(r"^##\s*" + re.escape(s), texto, re.M)]
    return {"secoes_ausentes": faltam, "por_preencher": len(re.findall(r"\[PREENCHER", texto)), "ok": not faltam and "[PREENCHER" not in texto}


def diario(linha, arquivo=None):
    if not linha.strip():
        raise ValueError("linha vazia")
    if arquivo:
        caminho = os.path.expanduser(arquivo)
    else:
        caminho = os.path.join(config.casa_orquestra(), "diario", datetime.now().strftime("%Y-%m-%d") + ".md")
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    novo_arq = not os.path.exists(caminho)
    with open(caminho, "a", encoding="utf-8") as f:
        if novo_arq and not arquivo:
            f.write("# Diário — %s\n\n" % datetime.now().strftime("%d/%m/%Y"))
        f.write("- %s %s\n" % (datetime.now().strftime("%H:%M"), " ".join(linha.split())))
    with db.conectar() as con:
        db.evento(con, "diario", linha[:200])
    return caminho
