import json
import os
import time
import unittest
from datetime import datetime

from orquestra import cli, db, painel, trabalhos
from tests.base import Base
from tests.test_executores import asst, user

CLI = "aaaaaaaa-1111-2222-3333-444444444444"


def criou_pr(repo, n):
    cmd = {"type": "assistant", "message": {"id": "m9", "role": "assistant", "model": "claude-sonnet-5-5", "content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "gh pr create --title x"}}]}}
    res = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "https://github.com/%s/pull/%d\n" % (repo, n)}]}}
    return [user("a"), json.dumps(cmd), json.dumps(res)]


def terminado(**kw):
    t = {"estado": "terminado", "sessao": "local_x", "cli": "cli-x", "pr": None, "protegida": False, "etapas": {}, "ultima_atividade": time.time() - 7200}
    t.update(kw)
    return t


class TestRegraDeArquivar(unittest.TestCase):
    def test_terminada_sem_pr_aberto_arquiva(self):
        self.assertEqual(trabalhos.arquivavel(terminado())[0], True)

    def test_nunca_arquiva_fixada_rodando_pr_aberto_esperando_orquestrador_ou_recente(self):
        casos = {"fixada": (terminado(), {"local_x"}),
                 "rodando": (terminado(estado="rodando"), ()),
                 "pr aberto": (terminado(pr={"numero": 9, "estado": "OPEN"}), ()),
                 "esperando": (terminado(estado="esperando_dono"), ()),
                 "mãe": (terminado(protegida=True), ()),
                 "recente": (terminado(ultima_atividade=time.time() - 60), ())}
        for nome, (t, fix) in casos.items():
            sim, motivo = trabalhos.arquivavel(t, fix)
            self.assertFalse(sim, nome)
        self.assertEqual(trabalhos.arquivavel(terminado(), {"cli-x"})[1], "fixada")        # também pelo id do CLI

    def test_so_a_sessao_terminada_entra(self):
        for e in ("pr_aberto", "na_fila", "mesclado", "no_ar"):
            self.assertFalse(trabalhos.arquivavel(terminado(estado=e))[0], e)


class TestEstados(unittest.TestCase):
    def test_ciclo_completo(self):
        aberto, mesclado = {"estado": "OPEN"}, {"estado": "MERGED"}
        c = trabalhos.classificar
        self.assertEqual(c(True, False), "rodando")
        self.assertEqual(c(False, True), "esperando_dono")
        self.assertEqual(c(False, False, aberto), "pr_aberto")
        self.assertEqual(c(False, False, aberto, na_fila=True), "na_fila")
        self.assertEqual(c(False, False, mesclado), "mesclado")
        self.assertEqual(c(False, False, mesclado, noar=1.0), "no_ar")
        self.assertEqual(c(False, False, mesclado, noar=1.0, conferido=(1.0, "x")), "terminado")
        self.assertEqual(c(False, False), "terminado")


class TestStatus(Base):
    def app(self, cli_id, titulo, **extra):
        d = {"sessionId": "local_" + cli_id[:8], "cliSessionId": cli_id, "title": titulo, "isArchived": False, "model": "claude-sonnet-5-5"}
        d.update(extra)
        self.escreve("app/x/y/local_%s.json" % cli_id[:8], json.dumps(d))

    def transcript(self, cli_id, linhas, idade=7200):
        p = self.escreve("claude/projects/-p/%s.jsonl" % cli_id, "\n".join(linhas) + "\n")
        os.utime(p, (time.time() - idade,) * 2)

    def setUp(self):
        super().setUp()
        self.antigos = (trabalhos.pr_info, trabalhos.deploys)
        self.prs = {}
        trabalhos.pr_info = lambda n, agora=None, repo=None: self.prs.get(n)
        trabalhos.deploys = lambda agora=None: []
        trabalhos._ultimo["status"] = None

    def tearDown(self):
        trabalhos.pr_info, trabalhos.deploys = self.antigos
        super().tearDown()

    def test_pergunta_real_conta_relatorio_nao(self):
        self.app("aaaaaaaa-0", "Pergunta", postTurnSummary={"status_category": "blocked", "status_detail": "preciso do OK", "needs_action": "responder"},
                 postTurnSummaryFor="u1", lastAssistantUuid="u1", turnWrapUp={"description": "Escolha A ou B?"})
        self.app("bbbbbbbb-0", "Relatório", postTurnSummary={"status_category": "completed", "status_detail": "feito", "needs_action": ""},
                 postTurnSummaryFor="u2", lastAssistantUuid="u2", turnWrapUp={"description": "Tudo pronto, testes verdes."})
        self.app("cccccccc-0", "Resumo velho", postTurnSummary={"status_category": "blocked", "needs_action": "x"}, postTurnSummaryFor="u1", lastAssistantUuid="u9")
        for c in ("aaaaaaaa-0", "bbbbbbbb-0", "cccccccc-0"):
            self.transcript(c, [user("a"), asst("ok")])
        por = {t["titulo"]: t for t in trabalhos.status(fixadas=())["trabalhos"]}
        self.assertEqual((por["Pergunta"]["estado"], por["Relatório"]["estado"], por["Resumo velho"]["estado"]), ("esperando_dono", "terminado", "terminado"))
        self.assertEqual(por["Pergunta"]["ultima_frase"], "Escolha A ou B?")

    def test_ciclo_do_pr_com_horas_e_conferencia(self):
        self.app(CLI, "Conserto do gate")
        self.transcript(CLI, criou_pr("alecastione-code/seller-turbo-painel", 282))
        agora = time.time()
        criado = agora - 7200
        self.prs[282] = {"estado": "OPEN", "url": "u", "rascunho": False, "criado_em": criado}
        self.assertEqual(trabalhos.status()["trabalhos"][0]["estado"], "pr_aberto")
        self.escreve("fila-unica.out", "02:44 #282 revisado (fila)\n")
        self.assertEqual(trabalhos.status()["trabalhos"][0]["estado"], "na_fila")
        merg = agora - 3600
        self.prs[282] = {"estado": "MERGED", "url": "u", "rascunho": False, "criado_em": criado, "commit": "7196b3e" + "0" * 33, "mesclado_em": merg}
        trabalhos.deploys = lambda agora=None: [("7196b3eaaaa", merg + 600)]
        t = trabalhos.status()["trabalhos"][0]
        self.assertEqual(t["estado"], "no_ar")
        e = t["etapas"]
        self.assertEqual(set(e) >= {"pr_aberto", "mesclado", "no_ar"}, True)
        self.assertLess(e["pr_aberto"], e["mesclado"]); self.assertLess(e["mesclado"], e["no_ar"])     # pr_aberto = criação do PR no GitHub
        self.escreve("diario.md", "- %s #282 no ar. Conferido por Júlia no CRM\n" % datetime.now().strftime("%d/%m %H:%M"))
        t = trabalhos.status()["trabalhos"][0]
        self.assertEqual((t["estado"], t["conferido_por"]), ("terminado", "Júlia no CRM"))
        self.assertIn("conferido", t["etapas"])

    def test_status_json_arquivar_concluidos_e_log(self):
        self.app("dddddddd-0", "Terminada"); self.transcript("dddddddd-0", [user("a"), asst("pronto")])
        self.app("eeeeeeee-0", "Fixada"); self.transcript("eeeeeeee-0", [user("a"), asst("pronto")])
        self.app("ffffffff-0", "Seller Turbo continuação"); self.transcript("ffffffff-0", [user("a"), asst("pronto")])
        self.app("99999999-0", "Rodando agora"); self.transcript("99999999-0", [user("a"), asst("x")], idade=5)
        s = trabalhos.status(fixadas={"local_eeeeeeee"})
        self.assertEqual([a["titulo"] for a in s["arquivar"]], ["Terminada"])
        self.assertEqual(s["contagem"]["rodando"], 1)
        json.dumps(s)                                                       # serializável (orq status --json)
        trabalhos.registrar_arquivada("local_dddddddd", "terminada sem PR")
        self.assertEqual(trabalhos.status()["arquivadas_24h"][0]["motivo"], "terminada sem PR")
        saida = trabalhos.gravar_json()
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "home", "status.json")))
        self.assertIs(trabalhos.ultimo(), saida)

    def test_esperando_voce_so_com_pergunta_real(self):
        self.app("aaaaaaaa-0", "Pergunta", postTurnSummary={"status_category": "blocked", "needs_action": "x"}, postTurnSummaryFor="u1", lastAssistantUuid="u1")
        self.app("bbbbbbbb-0", "Relatório", postTurnSummary={"status_category": "completed"}, postTurnSummaryFor="u2", lastAssistantUuid="u2")
        itens = [{"id": "claude:" + c[:8], "tipo": "claude", "estado": "esperando_dono", "nome": n, "id_completo": c, "ultimo_movimento_s": 900, "vivo": True}
                 for c, n in (("aaaaaaaa-0", "Pergunta"), ("bbbbbbbb-0", "Relatório"))]
        self.assertEqual(len(painel.esperando(itens)), 2)                    # regra antiga: fim de turno
        for c in ("aaaaaaaa-0", "bbbbbbbb-0"):
            self.transcript(c, [user("a"), asst("ok")])
        self.assertEqual([w["titulo"] for w in painel.esperando(itens, trabalhos.status())], ["Pergunta"])

    def test_pr_da_sessao_vem_do_ramo_ou_do_gh_pr_create_nunca_do_ultimo_citado(self):
        from orquestra import sellerturbo
        antigo = sellerturbo._rodar
        self.addCleanup(setattr, sellerturbo, "_rodar", antigo)
        sellerturbo._rodar = lambda args, timeout=25: [{"number": 275}] if args[:2] == ["pr", "list"] and "qa/ramo-x" in args else []
        ramo = json.dumps({"type": "user", "timestamp": "2026-11-08T01:00:00Z", "gitBranch": "qa/ramo-x", "cwd": "/a", "message": {"role": "user", "content": "oi"}})
        self.app("11111111-0", "Dona do ramo"); self.transcript("11111111-0", [ramo, asst("cita https://github.com/alecastione-code/seller-turbo-painel/pull/999")])
        self.app("22222222-0", "Criou o PR"); self.transcript("22222222-0", criou_pr("alecastione-code/seller-turbo-painel", 283))
        self.app("33333333-0", "Só citou"); self.transcript("33333333-0", [user("a"), asst("revisei https://github.com/alecastione-code/seller-turbo-painel/pull/275")])
        self.app("44444444-0", "Orquestrador do painel (continuação)"); self.transcript("44444444-0", criou_pr("alecastione-code/seller-turbo-painel", 275))
        self.app("55555555-0", "Mãe: Ferramentas"); self.transcript("55555555-0", criou_pr("alecastione-code/seller-turbo-painel", 280))
        for n in (275, 283):
            self.prs[n] = {"estado": "OPEN", "url": "u", "criado_em": time.time() - 100}
        por = {t["titulo"]: (t["pr"] or {}).get("numero") for t in trabalhos.status()["trabalhos"]}
        self.assertEqual(por, {"Dona do ramo": 275, "Criou o PR": 283, "Só citou": None, "Orquestrador do painel (continuação)": None, "Mãe: Ferramentas": None})

    def test_regra_de_terminado_painel_exige_conferencia_outro_repo_e_sem_pr_nao(self):
        merg = time.time() - 3000
        self.app("66666666-0", "Painel mesclado"); self.transcript("66666666-0", criou_pr("alecastione-code/seller-turbo-painel", 301))
        self.app("77777777-0", "Outro repo"); self.transcript("77777777-0", criou_pr("alecastione-code/outro", 7))
        self.app("88888888-0", "Sem PR"); self.transcript("88888888-0", [user("a"), asst("pronto")])
        self.prs[301] = {"estado": "MERGED", "criado_em": merg - 600, "mesclado_em": merg, "commit": "abc1234" + "0" * 33}
        trabalhos.pr_info = lambda n, agora=None, repo=None: {"estado": "MERGED", "criado_em": merg - 600, "mesclado_em": merg, "commit": "abc1234", "url": "u"} if repo == "alecastione-code/outro" else self.prs.get(n)
        por = {t["titulo"]: t for t in trabalhos.status()["trabalhos"]}
        self.assertEqual(por["Painel mesclado"]["estado"], "mesclado")          # sem "#301 conferido" no diário não termina
        self.assertEqual(por["Outro repo"]["estado"], "terminado")
        self.assertEqual(por["Outro repo"]["etapas"]["terminado"][:16], datetime.fromtimestamp(merg).astimezone().isoformat()[:16])
        self.assertEqual(por["Sem PR"]["estado"], "terminado")
        self.escreve("diario.md", "- %s #301 conferido\n" % datetime.now().strftime("%d/%m %H:%M"))
        self.assertEqual({t["titulo"]: t["estado"] for t in trabalhos.status()["trabalhos"]}["Painel mesclado"], "terminado")

    def test_modelo_opus_nao_protege_e_turno_aberto_conta_como_rodando(self):
        self.app("99999999-1", "Trabalho em opus", model="claude-opus-5-5"); self.transcript("99999999-1", [user("a"), asst("pronto")])
        agora = time.time()
        self.app("aaaaaaaa-1", "Neem", latestUserFrameAt=int(agora * 1000) - 60000, lastTurnEdgeAt=int(agora * 1000) - 600000, lastActivityAt=int(agora * 1000) - 30000)
        self.transcript("aaaaaaaa-1", [user("a"), asst("pronto")])
        s = trabalhos.status()
        self.assertEqual([a["titulo"] for a in s["arquivar"]], ["Trabalho em opus"])
        self.assertEqual({t["titulo"]: t["estado"] for t in s["trabalhos"]}["Neem"], "rodando")

    def test_orq_status_json(self):
        self.app(CLI, "Qualquer"); self.transcript(CLI, [user("a"), asst("ok")])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["status", "--json"]), 0)
        r = json.loads(buf.getvalue())
        self.assertEqual(set(r) >= {"trabalhos", "contagem", "arquivar", "concluidos_hoje", "arquivadas_24h"}, True)
        self.assertEqual(set(r["trabalhos"][0]) >= {"titulo", "sessao", "pr", "estado", "etapas", "ultima_frase"}, True)


if __name__ == "__main__":
    unittest.main()
