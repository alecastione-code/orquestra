import io
import json
import os
import time
import unittest
from datetime import datetime

from orquestra import cli, db, melhorias
from tests.base import Base


def orq(*a):
    out = io.StringIO()
    return cli.main(list(a), out), out.getvalue()


def bash(cmd, i=[0]):
    i[0] += 1
    return json.dumps({"message": {"role": "assistant", "content": [{"type": "tool_use", "id": "b%d" % i[0], "name": "Bash", "input": {"command": cmd}}]}}) + "\n"


class TestHeuristicas(Base):
    def chaves(self, periodo="dia"):
        melhorias.analisar(periodo)
        return {m["chave"].split(":")[0] for m in melhorias.lista()}

    def test_comando_repetido_3x_e_segredo_redigido(self):
        cmd = 'curl -s -H "Authorization: Bearer SEGREDO123" https://x/api/pedidos'
        self.escreve("claude/projects/p/s.jsonl", bash(cmd) * 3 + bash("ls") * 5)
        melhorias.analisar()
        ms = melhorias.lista()
        rep = [m for m in ms if m["chave"].startswith("repetido-cmd")]
        self.assertEqual(len(rep), 1)
        self.assertNotIn("SEGREDO123", json.dumps(ms))  # nunca persiste segredo
        self.assertIn("3 vezes", rep[0]["evidencia"])

    def test_tarefa_repetida_3x(self):
        for i in range(3):
            db.tarefa_nova("Conferir fila %d no ar" % i, nivel=1)
        self.assertIn("repetido-tarefa", self.chaves())

    def test_espera_sem_vigia_quando_dono_pergunta_estado(self):
        db.msg("dono", "orquestrador", "como está o T477? já terminou?")
        self.assertIn("espera-sem-vigia", self.chaves())

    def test_tarefa_barata_que_voltou_2x(self):
        t = db.tarefa_nova("x", nivel=1)
        with db.conectar() as con:
            con.execute("UPDATE tarefas SET voltas=2 WHERE id=?", (t,))
        ms = {m["chave"] for m in (melhorias.analisar() or [])}
        self.assertIn("voltou2:%d" % t, ms)

    def test_tarefa_cara_bracal(self):
        db.tarefa_nova("varrer logs do mês", nivel=3, tipo="leitura")
        self.assertIn("caro-bracal", self.chaves())

    def test_skill_nunca_usada(self):
        for n in ("a", "b", "c", "d"):
            self.escreve("agents/%s/SKILL.md" % n, "---\nname: %s\ndescription: faz %s\n---\n" % (n, n))
            os.symlink(os.path.join(self.tmp, "agents", n), os.path.join(self.env["ORQUESTRA_SKILLS"], n))
        uso = json.dumps({"message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "a"}}]}}) + "\n"
        self.escreve("claude/projects/p/s.jsonl", uso)
        melhorias.analisar()
        m = [x for x in melhorias.lista() if x["chave"] == "skills-sem-uso"][0]
        self.assertIn("b, c, d", m["evidencia"])
        self.assertNotIn("a,", m["evidencia"])

    def test_cota_fora_do_ritmo(self):
        agora = time.time()
        with db.conectar() as con:
            con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em) VALUES ('codex',50,?, 'x',?)", (agora + 3 * 86400, agora - 90000))
        db.cota_registrar("codex", 70, agora + 3 * 86400)
        self.assertIn("cota-ritmo", self.chaves())

    def test_travamento_e_gate_repetido(self):
        with db.conectar() as con:
            db.evento(con, "alerta", "TRAVADA codex:1 — x")
            db.evento(con, "alerta", "GATE FALHOU no PR #5 (qa/x): Release tests 1/2")
            db.evento(con, "alerta", "GATE FALHOU no PR #6 (fix/y): Release tests 1/2")
        ch = self.chaves()
        self.assertIn("travamentos", ch)
        self.assertIn("gate", ch)

    def test_correcao_do_dono_vira_sugestao_e_segredo_nao_vaza(self):
        self.escreve("claude/projects/p/memory/regra.md", "---\nname: regra-x\ndescription: sempre conferir token=ABC999 no ar\nmetadata:\n  type: feedback\n---\ncorpo\n")
        melhorias.analisar()
        ms = melhorias.lista()
        self.assertTrue([m for m in ms if m["chave"].startswith("feedback")])
        self.assertNotIn("ABC999", json.dumps(ms))

    def test_nao_duplica_na_segunda_analise(self):
        db.msg("dono", "orquestrador", "status?")
        self.assertIn("espera-sem-vigia", {m["chave"] for m in melhorias.analisar()})
        self.assertEqual(len(melhorias.analisar()), 0)


class TestCicloDaMelhoria(Base):
    def nova(self):
        orq("melhoria", "nova", "--problema", "Vigia sem aviso em produção", "--proposta", "criar avisador", "--executor", "Haiku", "--ganho", "tempo")
        return melhorias.lista()[0]

    def test_aprovar_cria_tarefa_no_nivel_do_executor(self):
        m = self.nova()
        _, saida = orq("melhoria", "aprovar", str(m["id"]))
        t = db.tarefa(melhorias._get(m["id"])["tarefa_id"])
        self.assertEqual((t["nivel"], t["estado"], t["projeto"]), (1, "nova", "orquestra"))
        self.assertIn("criar avisador", t["brief"])
        self.assertEqual(melhorias._get(m["id"])["estado"], "aprovada")
        self.assertEqual(orq("melhoria", "aprovar", str(m["id"]))[0], 1)  # não aprova duas vezes
        orq("melhoria", "feita", str(m["id"]))
        self.assertEqual(melhorias._get(m["id"])["estado"], "feita")

    def test_recusar_nao_cria_tarefa(self):
        m = self.nova()
        orq("melhoria", "recusar", str(m["id"]))
        self.assertEqual(melhorias._get(m["id"])["estado"], "recusada")
        self.assertEqual(db.tarefas(), [])

    def test_lista_cli_json(self):
        self.nova()
        self.assertEqual(json.loads(orq("melhorias", "lista", "--json")[1])[0]["estado"], "sugerida")


class TestRegraDeDecisao(Base):
    def test_risco_alto_vai_para_o_dono_e_baixo_e_aplicado(self):
        base = dict(chave="k", evidencia="", ganho="", custo="baixo", executor="Sonnet", prioridade=1)
        casos = {"alto": ["Ajustar o preço dos anúncios", "Mudar Ads da loja", "Corrigir em produção", "Dar permissão ao agente",
                          "Guardar o token no brief", "Apagar logs antigos", "Desativar skill X", "Mudar o jeito do dono trabalhar"],
                 "baixo": ["Criar avisador de gate", "Adicionar campo ao brief", "Script para trabalho repetido", "Ajustar o cérebro com evidência", "Atualizar docs"]}
        for esperado, textos in casos.items():
            for t in textos:
                s = dict(base, problema=t, proposta=t, risco="baixo")
                self.assertEqual(melhorias.classificar_risco(s), esperado, t)
                self.assertEqual(melhorias.decidir(s), "sugerida" if esperado == "alto" else "aplicada", t)
        self.assertEqual(melhorias.classificar_risco(dict(base, problema="x", proposta="y", risco="alto")), "alto")  # marcação explícita vence

    def test_baixo_risco_e_aplicado_na_hora_com_tarefa_aviso_e_mensagem(self):
        orq("melhoria", "nova", "--problema", "Criar avisador de gate", "--proposta", "script que avisa", "--executor", "Sonnet")
        m = melhorias.lista()[0]
        self.assertEqual((m["estado"], m["risco"]), ("aplicada", "baixo"))
        self.assertEqual(db.tarefa(m["tarefa_id"])["estado"], "nova")
        self.assertTrue([e for e in db.eventos() if e["tipo"] == "melhoria-aplicada"])
        self.assertTrue([x for x in db.msgs("orquestrador") if "aplicada sozinha" in x["texto"]])

    def test_alto_risco_fica_sugerido_sem_tarefa(self):
        orq("melhoria", "nova", "--problema", "Mexer no preço do Fiprolex", "--proposta", "subir 1%")
        m = melhorias.lista()[0]
        self.assertEqual((m["estado"], m["risco"], m["tarefa_id"]), ("sugerida", "alto", None))
        self.assertEqual(db.tarefas(), [])

    def test_banco_antigo_ganha_o_estado_aplicada(self):
        import sqlite3
        db.conectar().close()
        raw = sqlite3.connect(os.environ["ORQUESTRA_DB"])
        raw.executescript("DROP TABLE melhorias; CREATE TABLE melhorias (id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT NOT NULL, problema TEXT NOT NULL, "
                          "evidencia TEXT DEFAULT '', proposta TEXT DEFAULT '', ganho TEXT DEFAULT '', custo TEXT DEFAULT '', executor TEXT DEFAULT '', "
                          "risco TEXT DEFAULT '', prioridade REAL NOT NULL DEFAULT 0, estado TEXT NOT NULL DEFAULT 'sugerida' "
                          "CHECK(estado IN ('sugerida','aprovada','recusada','feita')), tarefa_id INTEGER, origem TEXT DEFAULT 'analise', "
                          "criado REAL NOT NULL, atualizado REAL NOT NULL); INSERT INTO melhorias(chave,problema,criado,atualizado) VALUES ('a','velha',1,1);")
        raw.commit(); raw.close()
        with db.conectar() as c:  # migra: preserva a linha e aceita 'aplicada'
            c.execute("UPDATE melhorias SET estado='aplicada'")
        self.assertEqual(melhorias.lista()[0]["problema"], "velha")


class TestBriefSkills(Base):
    def test_brief_traz_skills_obrigatorias_e_esforco_pela_hierarquia(self):
        from orquestra import brief
        t2 = brief.novo("Consertar X", 2)
        self.assertIn("ponytail (full)", t2); self.assertIn("karpathy-guidelines", t2); self.assertIn("ponytail-review", t2)
        self.assertIn("**padrão**", t2)
        t1 = brief.novo("Varrer logs", 1)
        self.assertIn("ponytail (ultra", t1); self.assertIn("**low**", t1)
        self.assertIn("**alto**", brief.novo("Desenhar", 3))
        self.assertEqual(brief.validar(t2)["secoes_ausentes"], [])


class TestRodadaDasVinteEDuas(Base):
    def test_so_roda_depois_das_22_e_uma_vez_por_dia(self):
        d = datetime.now().replace(hour=21, minute=59)
        self.assertFalse(melhorias.deve_rodar(d))
        d22 = d.replace(hour=22, minute=0)
        self.assertTrue(melhorias.deve_rodar(d22))
        os.environ["ORQUESTRA_SEM_NOTIFICAR"] = "1"
        melhorias.rodada_diaria(d22)
        self.assertFalse(melhorias.deve_rodar(d22.replace(hour=23)))
        self.assertIsNone(melhorias.rodada_diaria(d22.replace(hour=23)))
        os.environ.pop("ORQUESTRA_SEM_NOTIFICAR")


if __name__ == "__main__":
    unittest.main()
