import json
import os
import time
import unittest
from datetime import datetime, timezone

from orquestra import aovivo, coletores, custos, db, desperdicio, executores, painel, servidor, transcritos
from tests.base import Base


def iso(seg_atras=0):
    return datetime.fromtimestamp(time.time() - seg_atras, timezone.utc).isoformat().replace("+00:00", "Z")


def asst(texto, modelo="claude-sonnet-5-5", seg=0, i=100, o=500, cr=20_000, cw=1_000, mid="m1", stop="end_turn", usa_ferramenta=False):
    c = [{"type": "text", "text": texto}]
    if usa_ferramenta:
        c.append({"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}})
    return json.dumps({"type": "assistant", "timestamp": iso(seg), "message": {"id": mid, "role": "assistant", "model": modelo, "stop_reason": stop, "content": c,
                       "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}})


def user(texto, seg=0):
    return json.dumps({"type": "user", "timestamp": iso(seg), "cwd": "/a/seller-turbo-t9", "message": {"role": "user", "content": texto}})


class Corpo(Base):
    def sub(self, nome, sessao="mae00000-1111-2222-3333-444444444444", description="Receptor de avisos token=SEGREDO9", finished=True,
            prompt="Faça o receptor\ndetalhe", resposta="Linha 1 pronta\nLinha 2 testes verdes\nLinha 3 sem pendências\nLinha 4 sobra", modelo="claude-sonnet-5-5", idade=0):
        base = "claude/projects/-proj/%s/subagents/agent-%s" % (sessao, nome)
        p = self.escreve(base + ".jsonl", "\n".join([user(prompt, 900), asst(resposta, modelo, 600, mid="a")]) + "\n")
        self.escreve(base + ".meta.json", json.dumps({"description": description, "finished": finished, "model": "sonnet"}))
        t = time.time() - idade
        os.utime(p, (t, t))
        return p

    def mae(self, linhas, sessao="mae00000-1111-2222-3333-444444444444", idade=0):
        p = self.escreve("claude/projects/-proj/%s.jsonl" % sessao, "\n".join(linhas) + "\n")
        t = time.time() - idade
        os.utime(p, (t, t))
        return p


class TestTarefaPorExecutor(Corpo):
    def test_subagente_vira_tarefa_com_todos_os_campos(self):
        self.sub("abc1")
        r = executores.sincronizar()
        self.assertEqual((r["novas"], r["subagentes"]), (1, 1))
        (t,) = db.tarefas()
        self.assertEqual(t["auto"], 1)
        self.assertEqual(t["titulo"], "Receptor de avisos [oculto]")          # description do meta, com segredo redigido
        self.assertEqual((t["modelo"], t["nivel"], t["estado"]), ("claude-sonnet-5-5", 2, "feita"))
        self.assertEqual(t["sessao_mae"], "mae00000-1111-2222-3333-444444444444")
        self.assertEqual((t["tokens_in"], t["tokens_out"], t["tokens_cache"]), (1100, 500, 20000))
        self.assertGreater(t["custo_brl"], 0)
        self.assertLess(t["iniciado"], t["concluido"])
        self.assertEqual(t["resultado"].splitlines(), ["Linha 1 pronta", "Linha 2 testes verdes", "Linha 3 sem pendências"])   # 3 linhas, a 4ª fica de fora

    def test_sem_description_usa_a_primeira_linha_do_prompt_e_oculta_segredo(self):
        self.sub("abc2", description="", prompt="# Conserte o login api_key=ABCDEF123456\nresto")
        executores.sincronizar()
        self.assertEqual(db.tarefas()[0]["titulo"], "Conserte o login [oculto]")

    def test_idempotente_e_atualiza_quando_termina(self):
        p = self.sub("abc3", finished=False)
        self.assertEqual(executores.sincronizar()["novas"], 1)
        self.assertEqual(db.tarefas()[0]["estado"], "pega")                       # mexeu agora e não terminou: rodando
        r = executores.sincronizar()
        self.assertEqual((r["novas"], r["atualizadas"]), (0, 0))
        self.escreve("claude/projects/-proj/mae00000-1111-2222-3333-444444444444/subagents/agent-abc3.meta.json", json.dumps({"description": "X", "finished": True}))
        r = executores.sincronizar()
        self.assertEqual((r["novas"], r["atualizadas"], len(db.tarefas())), (0, 1, 1))
        self.assertEqual(db.tarefas()[0]["estado"], "feita")

    def test_subagente_parado_sem_resposta_final_fica_bloqueado(self):
        self.sub("abc4", finished=False, resposta="", idade=3600)
        executores.sincronizar()
        t = db.tarefas()[0]
        self.assertEqual(t["estado"], "bloqueada")
        self.assertIn("parou sem resposta final", t["resultado"])

    def test_rollout_solto_do_codex_vira_tarefa_e_o_do_lancar_nao_duplica(self):
        from tests.test_codex_painel import rollout
        self.escreve("codex/sessions/2026/11/07/rollout-2026-11-07T10-00-00-aaaa0001.jsonl", rollout("s1", prompt="# Solto token=XYZ12345 faz coisa", seg=7200))
        self.escreve("codex/sessions/2026/11/07/rollout-2026-11-07T10-00-00-bbbb0002.jsonl", rollout("s2", prompt="# Lançado pelo orq", seg=0))
        # tarefa do `orq lancar` que começou na mesma hora do 2º rollout
        tid = db.tarefa_nova("Lançado pelo orq", 2, "p", "Codex gpt-6-sol")
        with db.conectar() as con:
            con.execute("UPDATE tarefas SET agente='codex-O1', estado='pega', iniciado=? WHERE id=?", (time.time() - 600, tid))
        r = executores.sincronizar()
        auto = [t for t in db.tarefas() if t["auto"]]
        self.assertEqual(r["codex"], 1)                                          # só o solto entra; o do lancar completa a tarefa existente
        self.assertEqual(len(auto), 2 - 1)
        self.assertEqual(db.tarefa(tid)["tokens_out"], 5000)
        self.assertEqual(auto[0]["executor"].startswith("Codex"), True)
        self.assertNotIn("XYZ12345", auto[0]["titulo"])

    def test_automaticas_nao_poluem_ao_vivo_nem_o_placar_de_entrega(self):
        self.sub("abc5", finished=False)
        executores.sincronizar()
        snap = aovivo.AMOSTRADOR.amostrar("", sessoes=[], buscar_energia=False)
        self.assertEqual([i for i in snap["itens"] if i["id"].startswith("tarefa:")], [])
        self.assertEqual(db.placar(), [])
        (r,) = db.placar_executores()
        self.assertEqual((r["modelo"], r["total"], r["rodando"]), ("claude-sonnet-5-5", 1, 1))


class TestCustoPorPapel(Corpo):
    def test_orquestrador_x_executores_e_economia_so_dos_executores(self):
        self.mae([user("oi", 900), asst("pronto", "claude-opus-5-5", 100, i=1000, o=2000, cr=0, cw=0)])
        self.sub("abc6", modelo="claude-sonnet-5-5")
        self.sub("abc7", modelo="claude-opus-5-5")                              # subagente em Opus continua sendo executor
        transcritos.atualizar()
        executores.sincronizar()
        rel = custos.relatorio("dia", "modelo")
        pp = rel["por_papel"]
        self.assertEqual(pp["orquestrador"]["sessoes"], 1)
        self.assertEqual(pp["executores"]["sessoes"], 2)
        self.assertGreater(pp["orquestrador"]["brl"], 0)
        self.assertAlmostEqual(pp["orquestrador"]["brl"] + pp["executores"]["brl"], rel["total_brl"], places=1)
        self.assertGreater(rel["economia_vs_orquestrador"], 0)                   # o Sonnet custou menos que o Opus equivalente
        por_tarefa = custos.relatorio("dia", "tarefa")["agrupado"]
        self.assertTrue(any(a["chave"].startswith("#") for a in por_tarefa))

    def test_desperdicio_aponta_subagente_caro(self):
        self.sub("abc8", modelo="claude-fable-5-1")
        executores.sincronizar()
        with db.conectar() as con:
            con.execute("UPDATE tarefas SET custo_brl=3.0")
        (a,) = desperdicio.executores_caros()
        self.assertIn("Sonnet", a["sugestao"])


class TestContextoEEspera(Corpo):
    def itens(self):
        aovivo.AMOSTRADOR.__init__()
        return aovivo.AMOSTRADOR.amostrar("", sessoes=coletores.claude_sessoes(), buscar_energia=False)["itens"]

    def test_esperando_voce_ordem_link_e_some_ao_voltar_a_rodar(self):
        os.makedirs(os.path.join(self.tmp, "app", "x", "y"))
        self.escreve("app/x/y/local_abc-123.json", json.dumps({"title": "Sessão velha", "cliSessionId": "velha000-1111-2222-3333-444444444444"}))
        os.environ["ORQUESTRA_APP_SESSIONS"] = os.path.join(self.tmp, "app")
        self.addCleanup(os.environ.pop, "ORQUESTRA_APP_SESSIONS", None)
        coletores._titulos_app.update(mtime=0, v={}, ids={})
        self.mae([user("a", 5000), asst("Posso seguir com o plano B?\ntoken=SEGREDO1")], "velha000-1111-2222-3333-444444444444", idade=1800)
        nova = self.mae([user("a", 5000), asst("Terminei, o que faço agora?")], "nova0000-1111-2222-3333-444444444444", idade=300)
        w = painel.esperando(self.itens())
        self.assertEqual([x["titulo"] for x in w], ["Sessão velha", w[1]["titulo"]])      # a mais antiga primeiro
        self.assertEqual(w[0]["link"], "claude://claude.ai/epitaxy/local_abc-123")
        self.assertIsNone(w[1]["link"])                                                   # o app não conhece: sem link, só o título
        self.assertTrue(w[0]["frase"].startswith("Posso seguir com o plano B?"))
        self.assertNotIn("SEGREDO1", w[0]["frase"])
        self.assertGreater(w[0]["parada_s"], w[1]["parada_s"])
        with open(nova, "a") as f:                                                         # a sessão volta a trabalhar
            f.write(asst("rodando", usa_ferramenta=True, stop="tool_use", mid="m2") + "\n")
        self.assertEqual(len(painel.esperando(self.itens())), 1)

    def test_resumo_do_dia_tem_5_linhas_e_a_visao_traz_tudo(self):
        self.mae([user("a", 900), asst("pronto", "claude-opus-5-5", 100)])
        self.sub("abc9")
        transcritos.atualizar()
        executores.sincronizar()
        r = painel.resumo_dia(self.itens(), db.cotas())
        self.assertEqual(len(r["linhas"]), 5)
        self.assertTrue(r["linhas"][0].startswith("Gasto equivalente hoje: R$"))
        self.assertIn("orquestrador", r["linhas"][0]); self.assertIn("executores", r["linhas"][0])
        self.assertIn("1 automáticas dos executores", r["linhas"][1])
        self.assertTrue(r["linhas"][2].startswith("Gate verde de 1ª"))
        self.assertTrue(r["linhas"][3].startswith("Esperando você"))
        self.assertTrue(r["linhas"][4].startswith("Cotas"))
        v = servidor.visao()
        for k in ("resumo_dia", "esperando", "contexto", "placar_auto"):
            self.assertIn(k, v)


class TestMensagensLidas(Base):
    def test_botao_marcar_todas_como_lidas(self):
        db.msg("a", "orquestrador", "um"); db.msg("a", "orquestrador", "dois")
        r = servidor.api_post("/api/mensagens", {"acao": "lidas"})
        self.assertEqual(r["marcadas"], 2)
        self.assertEqual([m["lida"] for m in db.msgs()], [1, 1])


if __name__ == "__main__":
    unittest.main()
