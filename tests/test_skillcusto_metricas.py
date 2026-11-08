import json
import os
import time
import unittest
from datetime import datetime, timezone

from orquestra import db, melhorias, metricas, skillcusto, transcritos
from tests.base import Base
from tests.test_custos import BaseCustos, agora_iso, orq


def asst(mid, modelo="claude-sonnet-5-5", o=100, i=10, cr=1000, cw=0, tools=()):
    c = [{"type": "tool_use", "id": "t" + mid, "name": n, "input": inp} for n, inp in tools]
    return json.dumps({"cwd": "/p/proj", "timestamp": agora_iso(), "message": {"id": mid, "role": "assistant", "model": modelo, "content": c,
                       "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}) + "\n"


def user(texto):
    return json.dumps({"timestamp": agora_iso(), "message": {"role": "user", "content": texto}}) + "\n"


class TestAtribuicaoDeSkill(BaseCustos):
    def skill(self, nome, kb=7, refs=()):
        d = os.path.join(self.tmp, "agents", "skills", nome)
        self.escreve("agents/skills/%s/SKILL.md" % nome, "---\nname: %s\ndescription: faz coisa\n---\n%s" % (nome, "x" * (kb * 1000)))
        for r in refs:
            self.escreve("agents/skills/%s/references/%s" % (nome, r), "y" * 3500)
        os.symlink(d, os.path.join(self.env["ORQUESTRA_SKILLS"], nome))
        return d

    def test_carregar_x_trabalho_guiado_ate_o_proximo_turno_do_dono(self):
        d = self.skill("minha-skill", kb=7, refs=["a.md"])
        ref = os.path.join(d, "references", "a.md")
        self.escreve("claude/projects/-p/sess0001.jsonl",
                     user("faz o X") + asst("m0", o=5) +                                        # antes da skill: não conta
                     asst("m1", o=50, tools=[("Skill", {"skill": "minha-skill"})]) +
                     asst("m2", o=200, cr=5000, tools=[("Read", {"file_path": ref})]) +
                     asst("m3", o=300, cr=6000, tools=[("Read", {"file_path": ref})]) +       # leitura repetida
                     user("agora outra coisa") + asst("m4", o=999))                              # depois do turno do dono: não conta
        transcritos.atualizar()
        r = {x["skill"]: x for x in skillcusto.relatorio("dia")["skills"]}["minha-skill"]
        self.assertEqual(r["usos"], 1)
        self.assertAlmostEqual(r["tokens_carregar"], (7000 + len("---\nname: minha-skill\ndescription: faz coisa\n---\n")) / 3.5 + 2 * 3500 / 3.5, delta=3500 / 3.5 + 20)
        self.assertEqual(r["tokens_guiado"], (10 + 200 + 5000) + (10 + 300 + 6000))  # só m2+m3: a mensagem que chama a skill (m1) vem antes da carga; m0 e m4 fora
        self.assertEqual(r["repetidos"], 1)
        self.assertFalse(r["abandonadas"])
        self.assertGreater(r["brl_carregar"], 0); self.assertGreater(r["brl_guiado"], 0)
        self.assertAlmostEqual(r["brl_total"], r["brl_carregar"] + r["brl_guiado"], places=1)

    def test_comando_do_dono_e_skill_abandonada(self):
        self.skill("do-dono")
        self.escreve("claude/projects/-p/sess0002.jsonl",
                     user("<command-name>/do-dono</command-name> faça") + asst("a1", o=20) + user("deixa pra lá") + asst("a2", o=500))
        transcritos.atualizar()
        r = {x["skill"]: x for x in skillcusto.relatorio("dia")["skills"]}["do-dono"]
        self.assertEqual((r["via_dono"], r["abandonadas"]), (1, 1))  # carregada e logo abandonada

    def test_resultado_associado_e_custo_por_resultado(self):
        self.skill("s1")
        self.escreve("claude/projects/-p/abcdef12zz.jsonl", user("x") + asst("m1", tools=[("Skill", {"skill": "s1"})]) + asst("m2", o=400))
        t = db.tarefa_nova("Consertar", nivel=2); db.tarefa_pegar(t, "claude:abcdef12")
        db.tarefa_feita(t, "ok"); db.tarefa_revisada(t)
        transcritos.atualizar()
        r = {x["skill"]: x for x in skillcusto.relatorio("dia")["skills"]}["s1"]
        self.assertEqual((r["resultados"], r["gates_verdes"]), (1, 1))
        self.assertAlmostEqual(r["custo_por_resultado"], r["brl_total"], places=2)

    def test_leitura_direta_do_skill_md_tambem_conta(self):
        d = self.skill("lida", kb=3)
        self.escreve("claude/projects/-p/s3.jsonl", user("x") + asst("m1", tools=[("Read", {"file_path": os.path.join(d, "SKILL.md")})]) + asst("m2"))
        transcritos.atualizar()
        self.assertEqual({x["skill"] for x in skillcusto.relatorio("dia")["skills"]}, {"lida"})


class TestRecomendacao(unittest.TestCase):
    base = dict(usos=5, abandonadas=0, via_dono=0, repetidos=0, resultados=2, tamanho_md=3000, media_por_uso=1.0)

    def test_pausar_custo_alto_sem_resultado_e_sem_uso_do_dono(self):
        self.assertEqual(skillcusto._recomendar(dict(self.base, resultados=0), 1.0, 15.0)[0], "pausar")

    def test_skill_do_dono_nunca_vira_pausar_so_otimizar(self):
        acao, porque = skillcusto._recomendar(dict(self.base, resultados=0, via_dono=2, repetidos=4), 1.0, 15.0)
        self.assertEqual(acao, "otimizar")
        self.assertIn("dono", porque)
        self.assertEqual(skillcusto._recomendar(dict(self.base, resultados=0, via_dono=2), 1.0, 15.0)[0], "manter")

    def test_otimizar_por_tamanho_repeticao_abandono_ou_custo(self):
        for extra in (dict(tamanho_md=20000), dict(repetidos=3), dict(abandonadas=3), dict(media_por_uso=5.0)):
            self.assertEqual(skillcusto._recomendar(dict(self.base, **extra), 1.0, 2.0)[0], "otimizar", extra)

    def test_manter_quando_normal_ou_sem_uso(self):
        self.assertEqual(skillcusto._recomendar(self.base, 1.0, 2.0)[0], "manter")
        self.assertEqual(skillcusto._recomendar(dict(self.base, usos=0), 1.0, 0)[0], "manter")


class TestOtimizarEMelhorias(BaseCustos):
    def test_otimizar_aplica_sozinho_e_pausar_so_sugere(self):
        d = os.path.join(self.tmp, "agents", "skills", "grande")
        self.escreve("agents/skills/grande/SKILL.md", "x" * 20000)
        os.symlink(d, os.path.join(self.env["ORQUESTRA_SKILLS"], "grande"))
        linhas = user("a") + asst("g1", tools=[("Skill", {"skill": "grande"})]) + asst("g2", o=300) + user("b")
        self.escreve("claude/projects/-p/s1.jsonl", linhas * 3)
        transcritos.atualizar()
        r = skillcusto.otimizar("grande")
        m = melhorias._get(r["melhoria"])
        self.assertEqual((m["estado"], m["risco"]), ("aplicada", "baixo"))   # edição de texto, reversível → aplica
        self.assertIn("references/", r["proposta"])
        self.assertEqual(db.tarefa(m["tarefa_id"])["estado"], "nova")
        with self.assertRaises(KeyError):
            skillcusto.otimizar("inexistente")
        # pausar: sempre sugerida (desliga skill)
        s = melhorias._s("skill-pausar:x", "Skill x cara demais", "ev", "Pausar a skill x", "g", "c", "orquestrador", "alto", 5)
        mid = melhorias.registrar(s)
        self.assertEqual(melhorias._get(mid)["estado"], "sugerida")


class TestMetricas(BaseCustos):
    def test_cartoes_e_estados(self):
        cs, extra = metricas.cartoes()
        self.assertEqual([c["id"] for c in cs], ["tokens_pr", "gate_1a", "retrabalho", "perguntas", "tempo_ar", "correcoes"])
        self.assertTrue(all(len(c["serie"]) == 4 for c in cs))
        self.assertEqual(extra["semanas"][-1], "esta")
        # 3 tarefas revisadas: 1 quebrou o gate (67% < 80%) e 2 voltaram (67% > 15%)
        for entrega, voltas in (("primeira", 0), ("quebrou", 1), ("voltou", 1)):
            t = db.tarefa_nova("x", nivel=2); db.tarefa_pegar(t, "a"); db.tarefa_feita(t, "ok")
            with db.conectar() as con:
                con.execute("UPDATE tarefas SET estado='feita', entrega=?, voltas=?, gate_falhou=? WHERE id=?", (entrega, voltas, 1 if entrega == "quebrou" else 0, t))
        cs = {c["id"]: c for c in metricas.cartoes()[0]}
        self.assertEqual(cs["gate_1a"]["atual"], 67)
        self.assertEqual(cs["gate_1a"]["estado"], "ruim")
        self.assertEqual(cs["retrabalho"]["atual"], 67)
        self.assertEqual(cs["retrabalho"]["estado"], "ruim")

    def test_conta_perguntas_do_dono_nos_transcritos_do_orquestrador_e_no_quadro(self):
        self.escreve("claude/projects/-p/orq.jsonl", user("e aí, como tá?") + asst("o1", modelo="claude-opus-5-5") + user("eai") + user("faz o deploy"))
        self.escreve("claude/projects/-p/exec.jsonl", user("e aí") + asst("e1", modelo="claude-sonnet-5-5"))   # não é do orquestrador
        db.msg("dono", "orquestrador", "como tá o T477?")
        transcritos.atualizar()
        c = {c["id"]: c for c in metricas.cartoes()[0]}["perguntas"]
        self.assertEqual(c["atual"], 3)  # 2 no transcript do orquestrador + 1 no quadro
        self.assertEqual(c["estado"], "ruim")

    def test_tokens_do_orquestrador_por_pr(self):
        from unittest import mock
        hoje = datetime.now().strftime("%Y-%m-%d")
        self.escreve("claude/projects/-p/orq.jsonl", asst("o1", modelo="claude-opus-5-5", i=1000, o=1000, cr=0, cw=0))
        transcritos.atualizar()
        with mock.patch("orquestra.metricas.sellerturbo.prs_mesclados", return_value={hoje: 2}):
            c = {c["id"]: c for c in metricas.cartoes()[0]}["tokens_pr"]
        self.assertEqual(c["atual"], 1000)  # (1000+1000) tokens ÷ 2 PRs
        with mock.patch("orquestra.metricas.sellerturbo.prs_mesclados", return_value=None):
            self.assertEqual({c["id"]: c for c in metricas.cartoes()[0]}["tokens_pr"]["estado"], "sem dado")

    def test_analisador_usa_as_metricas_como_evidencia(self):
        t = db.tarefa_nova("x", nivel=2); db.tarefa_pegar(t, "a"); db.tarefa_feita(t, "ok")
        with db.conectar() as con:
            con.execute("UPDATE tarefas SET estado='feita', entrega='quebrou', gate_falhou=1 WHERE id=?", (t,))
        melhorias.analisar()
        m = [x for x in melhorias.lista() if x["chave"] == "metrica:gate_1a"]
        self.assertEqual(len(m), 1)
        self.assertIn("meta ≥ 80%", m[0]["evidencia"])

    def test_cli_metricas_e_custos_skills(self):
        self.assertIn("Gate verde", orq("metricas")[1])
        self.assertEqual(len(json.loads(orq("metricas", "--json")[1])["cartoes"]), 6)
        self.assertIn("Custo por skill", orq("skills", "custo", "--semana")[1])


if __name__ == "__main__":
    unittest.main()
