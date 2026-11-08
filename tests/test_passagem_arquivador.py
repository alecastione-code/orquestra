"""Aviso de passagem em 3 estados, filtro do alarme falso de gates e cartão do Arquivador."""
import json
import os
import time

from orquestra import painel, sellerturbo, trabalhos
from tests.base import Base
from tests.test_executores import asst, user

VELHO = "3ba59e0f-1111-2222-3333-444444444444"


class Sessoes(Base):
    def setUp(self):
        super().setUp()
        os.environ["ORQUESTRA_PASSAR_VEZ"] = os.path.join(self.tmp, "pv")
        self.addCleanup(os.environ.pop, "ORQUESTRA_PASSAR_VEZ", None)
        self.antigos = (trabalhos.pr_info, trabalhos.deploys)
        trabalhos.pr_info = lambda n, agora=None, repo=None: None
        trabalhos.deploys = lambda agora=None: []

    def tearDown(self):
        trabalhos.pr_info, trabalhos.deploys = self.antigos
        super().tearDown()

    def app(self, cli_id, titulo, criado=None, **extra):
        d = {"sessionId": "local_" + cli_id[:8], "cliSessionId": cli_id, "title": titulo, "isArchived": False,
             "createdAt": int((criado or time.time() - 86400) * 1000)}
        prompt = extra.pop("prompt", "a")
        d.update(extra)
        self.escreve("app/x/y/local_%s.json" % cli_id[:8], json.dumps(d))
        p = self.escreve("claude/projects/-p/%s.jsonl" % cli_id, "\n".join([user(prompt), asst("pronto")]) + "\n")
        os.utime(p, (time.time() - 7200,) * 2)

    def aviso(self, cli_id, tokens, ha_s):
        p = self.escreve("pv/%s.json" % cli_id, json.dumps({"avisado": 400000, "tokens": tokens}))
        os.utime(p, (time.time() - ha_s,) * 2)


class TestPassagem(Sessoes):
    def test_criando_logo_depois_do_aviso(self):
        self.app(VELHO, "Seller Turbo continuação")
        self.aviso(VELHO, 433_914, 300)
        (a,) = painel.alertas_contexto()
        self.assertEqual(a["estado"], "criando")
        self.assertEqual(a["texto"], "Seller Turbo continuação passou de 300 mil (434 mil); ele mesmo está criando a sessão nova")
        self.assertIsNone(a["link"])

    def test_passou_a_vez_mostra_a_nova_e_arquiva_o_velho_mesmo_protegido(self):
        self.app(VELHO, "Seller Turbo continuação")
        self.aviso(VELHO, 433_914, 3600)
        criado = time.time() - 3500
        self.app("c0d2098c-1111-2222-3333-444444444444", "Orquestrador do painel (continuação)", criado=criado,
                 spawnedFrom={"sessionId": "local_3ba59e0f"}, completedTurns=1, lineageDetached=True)
        (a,) = painel.alertas_contexto()
        self.assertEqual(a["estado"], "passou")
        self.assertEqual(a["texto"], "Seller Turbo continuação passou a vez para Orquestrador do painel (continuação) às %s"
                         % time.strftime("%H:%M", time.localtime(criado)))
        self.assertEqual(a["link"], "claude://claude.ai/epitaxy/local_c0d2098c")
        arq = {x["sessao"]: x["motivo"] for x in trabalhos.status()["arquivar"]}
        self.assertEqual(arq.get("local_3ba59e0f"), "passou a vez para Orquestrador do painel (continuação)")
        self.assertNotIn("local_3ba59e0f", {x["sessao"] for x in trabalhos.status(fixadas={"local_3ba59e0f"})["arquivar"]})

    def test_filho_vivo_segura_o_pai_e_filho_desligado_libera(self):
        self.app(VELHO, "Seller Turbo continuação")
        self.aviso(VELHO, 433_914, 3600)
        self.app("c0d2098c-1111-2222-3333-444444444444", "Nova", criado=time.time() - 3500, spawnedFrom={"sessionId": "local_3ba59e0f"}, completedTurns=3)
        self.app("dddd0000-1111-2222-3333-444444444444", "Terminada com filha", )
        self.app("eeee0000-1111-2222-3333-444444444444", "Filha arquivada", spawnedFrom={"sessionId": "local_dddd0000"}, isArchived=True)
        self.app("ffff0000-1111-2222-3333-444444444444", "Filha viva", spawnedFrom={"sessionId": "local_dddd0000"})
        st = trabalhos.status()
        arq = {x["sessao"] for x in st["arquivar"]}
        self.assertNotIn("local_3ba59e0f", arq)
        self.assertNotIn("local_dddd0000", arq)
        mot = {t["sessao"]: t["arquivar_motivo"] for t in st["trabalhos"]}
        self.assertEqual(mot["local_3ba59e0f"], "sessão filha viva: Nova")
        self.app("c0d2098c-1111-2222-3333-444444444444", "Nova", criado=time.time() - 3500, spawnedFrom={"sessionId": "local_3ba59e0f"}, completedTurns=3,
                 lineageDetached=True)                                       # detach_session "self" da nova
        self.assertIn("local_3ba59e0f", {x["sessao"] for x in trabalhos.status()["arquivar"]})

    def test_so_arquiva_a_velha_depois_do_primeiro_turno_da_nova(self):
        self.app(VELHO, "Seller Turbo continuação")
        self.aviso(VELHO, 433_914, 3600)
        self.app("c0d2098c-1111-2222-3333-444444444444", "Nova", criado=time.time() - 3500, spawnedFrom={"sessionId": "local_3ba59e0f"}, completedTurns=0)
        self.assertEqual(painel.alertas_contexto()[0]["estado"], "passou")
        self.assertNotIn("local_3ba59e0f", {x["sessao"] for x in trabalhos.status()["arquivar"]})

    def test_nova_pelo_prompt_leia_passagem_na_pasta_da_velha(self):
        repo = os.path.join(self.tmp, "orquestra")
        self.app("5b545ed6-1111-2222-3333-444444444444", "Orquestra: quadro", cwd="/scratch", writtenRepositories=[repo])
        self.aviso("5b545ed6-1111-2222-3333-444444444444", 300_261, 3600)
        self.app("aaf5aeb6-1111-2222-3333-444444444444", "Orquestra: continuar", criado=time.time() - 3000, cwd=repo, completedTurns=1,
                 spawnedFrom={"sessionId": "local_outra000"}, prompt="Leia `%s/PASSAGEM.md` e continue do Pendente 1" % repo)
        self.app("cccccccc-1111-2222-3333-444444444444", "Segunda sessão depois", criado=time.time() - 1000, cwd=repo, completedTurns=1,
                 prompt="Leia `%s/PASSAGEM.md` e continue do Pendente 1" % repo)
        self.app("dddddddd-1111-2222-3333-444444444444", "Só abriu a pasta", cwd="/y", shellFolders=[repo])
        self.aviso("dddddddd-1111-2222-3333-444444444444", 300_000, 3600)
        self.app("bbbbbbbb-1111-2222-3333-444444444444", "Outra pasta", criado=time.time() - 3000, cwd="/x", completedTurns=1,
                 prompt="Leia `/x/PASSAGEM.md` e continue do Pendente 1")
        a = {x["sessao"]: x for x in painel.alertas_contexto()}
        self.assertEqual((a["local_5b545ed6"]["estado"], a["local_5b545ed6"]["nova"]), ("passou", "local_aaf5aeb6"))   # a 1ª depois do aviso
        self.assertEqual(a["local_dddddddd"]["estado"], "nao_passou")                                                 # shellFolders não conta
        arq = {x["sessao"]: x["motivo"] for x in trabalhos.status()["arquivar"]}
        self.assertEqual(arq.get("local_5b545ed6"), "passou a vez para Orquestra: continuar")

    def test_quem_passou_a_vez_sai_de_esperando_voce(self):
        perg = dict(postTurnSummary={"status_category": "blocked", "needs_action": "x"}, postTurnSummaryFor="u", lastAssistantUuid="u")
        self.app(VELHO, "Seller Turbo continuação", **perg)
        self.aviso(VELHO, 433_914, 3600)
        self.app("c0d2098c-1111-2222-3333-444444444444", "Nova", criado=time.time() - 3500, spawnedFrom={"sessionId": "local_3ba59e0f"}, completedTurns=1)
        itens = [{"id": "claude:3ba59e0f", "tipo": "claude", "estado": "esperando_dono", "nome": "Seller Turbo continuação", "id_completo": VELHO, "ultimo_movimento_s": 900}]
        self.assertEqual(painel.esperando(itens, trabalhos.status()), [])

    def test_filha_antiga_nao_conta_como_passagem(self):
        self.app(VELHO, "Seller Turbo continuação")
        self.aviso(VELHO, 433_914, 600)
        self.app("abcdef00-1111-2222-3333-444444444444", "Executor de ontem", criado=time.time() - 86400 * 2,
                 spawnedFrom={"sessionId": "local_3ba59e0f"})
        self.assertEqual(painel.alertas_contexto()[0]["estado"], "criando")

    def test_mais_de_30_min_sem_sessao_nova_pede_o_dono(self):
        self.app(VELHO, "Seller Turbo continuação")
        self.aviso(VELHO, 433_914, 1900)
        (a,) = painel.alertas_contexto()
        self.assertEqual(a["estado"], "nao_passou")
        self.assertEqual(a["texto"], "Seller Turbo continuação não passou a vez; abra o chat e clique no card")
        self.assertEqual(a["link"], "claude://claude.ai/epitaxy/local_3ba59e0f")
        self.assertNotIn("local_3ba59e0f", {x["sessao"] for x in trabalhos.status()["arquivar"]})

    def test_sem_aviso_do_hook_nada_aparece(self):
        self.app(VELHO, "Seller Turbo continuação")
        self.assertEqual(painel.alertas_contexto(), [])


def pr(n, checks, rotulos=()):
    return {"number": n, "title": "t", "headRefName": "fix/x", "labels": [{"name": r} for r in rotulos],
            "statusCheckRollup": [{"name": a, "status": "COMPLETED", "conclusion": b} for a, b in checks]}


class TestGatesFalsos(Base):
    def test_recusa_fora_da_vez_nao_e_gate_falhando(self):
        prs = sellerturbo.resumir_prs([
            pr(287, [("afetados", "SUCCESS"), ("release-gate", "FAILURE")]),                      # recusa "exige a suíte completa"
            pr(286, [("Release tests 2/2", "FAILURE"), ("release-gate", "FAILURE")]),             # suíte falhou de verdade
            pr(285, [("release-gate", "FAILURE")], ["fila:vez"]),                                 # na vez: qualquer falha conta
            pr(284, [("Release tests 1/2", "CANCELLED")])])
        g = {p["numero"]: p["gate"] for p in prs}
        self.assertEqual(g, {287: "fora da vez", 286: "falhou", 285: "falhou", 284: "ok"})
        self.assertEqual(sellerturbo.alertas(prs, {287: "ok", 286: "ok", 285: "ok"}), [p for p in prs if p["numero"] in (286, 285)])


class TestArquivador(Sessoes):
    def test_rodadas_do_arquivador_ficam_fora_do_quadro(self):
        self.app("aaaa0000-1111-2222-3333-444444444444", "Orquestra: arquivar sessões terminadas", scheduledTaskId="orquestra-arquivador",
                 postTurnSummary={"status_category": "blocked", "needs_action": "x"}, postTurnSummaryFor="u", lastAssistantUuid="u")
        self.app("bbbb0000-1111-2222-3333-444444444444", "Trabalho normal")
        self.assertEqual([t["titulo"] for t in trabalhos.status()["trabalhos"]], ["Trabalho normal"])

    def test_cartao_com_ultima_rodada_arquivadas_puladas_proxima_e_tempo(self):
        rodada = time.time() - 600
        self.app("aaaa0000-1111-2222-3333-444444444444", "Orquestra: arquivar", criado=rodada, scheduledTaskId="orquestra-arquivador", isArchived=True)
        self.app("cccc0000-1111-2222-3333-444444444444", "Orquestrador do painel")         # terminado, mas protegido: pulado com motivo
        trabalhos.registrar_arquivada("local_dddd0000", "terminada sem PR aberto (arquivador automático)")
        trabalhos.registrar_arquivada("local_eeee0000", "à mão")
        trabalhos.registrar_tempo_status(0.42)
        c = trabalhos.arquivador()
        self.assertEqual(c["ultima"], rodada // 1)
        self.assertEqual(c["proxima"], rodada // 1 + 1800)
        self.assertEqual([a["sessao"] for a in c["arquivadas"]], ["local_dddd0000"])
        self.assertEqual(c["puladas"], [{"titulo": "Orquestrador do painel", "motivo": "orquestrador/mãe"}])
        self.assertFalse(c["atrasado"])
        self.assertAlmostEqual(c["tempo_status_s"], 0.42)

    def test_sem_rodada_ha_70_min_alerta_e_erro_aparece(self):
        self.app("aaaa0000-1111-2222-3333-444444444444", "Orquestra: arquivar", criado=time.time() - 4300, scheduledTaskId="orquestra-arquivador", isArchived=True)
        trabalhos.registrar_erro_arquivador("orq: comando não encontrado")
        c = trabalhos.arquivador()
        self.assertTrue(c["atrasado"])
        self.assertEqual(c["erro"]["texto"], "orq: comando não encontrado")

    def test_cli_status_json_mede_o_tempo_e_arquivador_erro(self):
        from orquestra import cli
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()) as f:
            cli.main(["status", "--json"])
        json.loads(f.getvalue())
        self.assertIsNotNone(trabalhos.arquivador()["tempo_status_s"])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["arquivador-erro", "gh caiu"])
        self.assertEqual(trabalhos.arquivador()["erro"]["texto"], "gh caiu")


class TestMarkdownNaFrase(Base):
    def test_frase_exibida_sem_markdown_cru(self):
        from orquestra.segredos import ocultar
        self.assertEqual(ocultar("**Pronto:** veja [#287](https://github.com/x/pull/287) e `orq status` token=abc123"),
                         "Pronto: veja #287 e orq status [oculto]")
