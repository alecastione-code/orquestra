import io
import json
import os
import re
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

from orquestra import brief, cli, db, esperar, hook, lancar
from tests.base import Base


def orq(*a):
    out = io.StringIO()
    return cli.main(list(a), out), out.getvalue()


class TestEsperar(Base):
    def test_exige_condicao(self):
        with self.assertRaises(ValueError):
            esperar.esperar()

    def test_feita_ja_pronta_retorna_na_hora(self):
        t = db.tarefa_nova("x", nivel=0); db.tarefa_pegar(t, "a"); db.tarefa_feita(t, "tudo certo")
        cod, linha = esperar.esperar(feita=t, timeout=5, dormir=lambda s: None)
        self.assertEqual(cod, 0)
        self.assertIn("FEITA #%d" % t, linha)

    def test_acorda_na_falha_e_na_travada_e_ignora_antigas(self):
        with db.conectar() as con:
            db.evento(con, "alerta", "FALHOU antigo")
        passos = []

        def dormir(s):
            passos.append(s)
            if len(passos) == 1:
                with db.conectar() as con:
                    db.evento(con, "alerta", "TRAVADA codex:1 — x")
                    db.evento(con, "alerta", "FALHOU codex:2 — y")
        cod, linha = esperar.esperar(falha=True, timeout=60, dormir=dormir)
        self.assertEqual((cod, linha), (0, "FALHOU codex:2 — y"))  # --falha não acorda por TRAVADA
        passos.clear()
        cod, linha = esperar.esperar(travada=True, timeout=60, dormir=dormir)
        self.assertEqual(cod, 0)
        self.assertIn("TRAVADA", linha)

    def test_merge_pela_fila_unica(self):
        self.escreve("logs/fila-unica.out", "17:43 #269 MERGED\n")
        def dormir(s):
            with open(os.path.join(self.tmp, "logs/fila-unica.out"), "a") as f:
                f.write("19:45 #271 MERGED b5f9322\n")
        cod, linha = esperar.esperar(merge=True, timeout=60, dormir=dormir)
        self.assertEqual(cod, 0)
        self.assertIn("#271 MERGED", linha)

    def test_timeout_devolve_codigo_2(self):
        relogio = [1000.0]
        def agora(): return relogio[0]
        def dormir(s): relogio[0] += 1000
        cod, linha = esperar.esperar(falha=True, timeout=1500, dormir=dormir, agora=agora)
        self.assertEqual(cod, 2)
        self.assertIn("TIMEOUT", linha)

    def test_parse_timeout(self):
        self.assertEqual(esperar.parse_timeout("55m"), 3300)
        self.assertEqual(esperar.parse_timeout("2h"), 7200)
        self.assertEqual(esperar.parse_timeout("10"), 600)


class TestLancar(Base):
    def setUp(self):
        super().setUp()
        self.codex = self.escreve("bin/codex", "#!/bin/sh\necho \"args: $@\" | head -c 400\nexit 3\n")
        os.chmod(self.codex, 0o755)
        os.environ["ORQUESTRA_CODEX_BIN"] = self.codex
        self.brief = self.escreve("brief.md", "# T900 — Fazer a coisa\n\nCorpo do brief.\n")
        os.makedirs(os.path.join(self.tmp, "wt"))

    def tearDown(self):
        os.environ.pop("ORQUESTRA_CODEX_BIN", None)
        super().tearDown()

    def test_cria_tarefa_lanca_grava_pid_e_log_com_codigo_de_saida(self):
        r = lancar.lancar(self.brief, "gpt-6-sol", os.path.join(self.tmp, "wt"))
        t = db.tarefa(r["tarefa"])
        self.assertEqual((t["estado"], t["nivel"], t["pid"], t["modelo"]), ("pega", 2, r["pid"], "gpt-6-sol"))
        for _ in range(40):
            time.sleep(0.1)
            if "exit 3" in open(r["log"]).read():
                break
        log = open(r["log"]).read()
        self.assertIn("exec -m gpt-6-sol --sandbox danger-full-access --skip-git-repo-check -C", log)
        self.assertIn("# T900 — Fazer a coisa", log)
        self.assertIn("exit 3", log)  # rodapé que o ao-vivo lê como código de saída

    def test_luna_e_nivel_1(self):
        r = lancar.lancar(self.brief, "gpt-6-luna", os.path.join(self.tmp, "wt"))
        self.assertEqual(db.tarefa(r["tarefa"])["nivel"], 1)

    def test_recusa_com_codex_ativo_e_cota_baixa_exceto_forcar(self):
        db.cota_registrar("codex", 70, time.time() + 86400 * 3)  # sobra 30%
        with mock.patch("orquestra.lancar.coletores.codex_processos", return_value=[{"pid": 1}]):
            with self.assertRaises(ValueError) as e:
                lancar.lancar(self.brief, "gpt-6-sol", os.path.join(self.tmp, "wt"))
            self.assertIn("< 40%", str(e.exception))
            self.assertEqual(db.tarefas(), [])  # nada foi criado
            lancar.lancar(self.brief, "gpt-6-sol", os.path.join(self.tmp, "wt"), forcar=True)
        with mock.patch("orquestra.lancar.coletores.codex_processos", return_value=[]):
            lancar.lancar(self.brief, "gpt-6-sol", os.path.join(self.tmp, "wt"))  # nenhum ativo: passa

    def test_modelo_e_pasta_invalidos(self):
        with self.assertRaises(ValueError):
            lancar.lancar(self.brief, "gpt-9", os.path.join(self.tmp, "wt"))
        with self.assertRaises(ValueError):
            lancar.lancar(self.brief, "gpt-6-sol", "/nao/existe")


class TestHook(Base):
    def test_registra_pulso_com_projeto_modelo_ferramenta(self):
        tr = self.escreve("t.jsonl", json.dumps({"message": {"model": "claude-sonnet-5-5"}}) + "\n")
        hook.processar(json.dumps({"session_id": "abcdef123456", "cwd": "/x/meu-projeto", "tool_name": "Bash", "transcript_path": tr}))
        p = db.pulsos_recentes()[0]
        self.assertEqual(p["agente"], "claude:abcdef12")
        self.assertEqual(p["nota"], "meu-projeto · claude-sonnet-5-5 · Bash")

    def test_script_e_silencioso_e_sempre_sai_com_zero(self):
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "hook-pulso.sh")
        for entrada in ("lixo que não é json", "", json.dumps({"session_id": "s1", "cwd": "/a/b", "hook_event_name": "Stop"})):
            r = subprocess.run([script], input=entrada, capture_output=True, text=True, env=dict(os.environ))
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(db.pulsos_recentes()[0]["agente"], "claude:s1")

    def test_sai_zero_mesmo_com_banco_inacessivel(self):
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "hook-pulso.sh")
        env = dict(os.environ, ORQUESTRA_DB="/proc/nao/pode/o.db")
        r = subprocess.run([script], input='{"session_id":"s"}', capture_output=True, text=True, env=env)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))


class TestHooksMd(Base):
    """O comando do JSON de docs/HOOKS.md tem de funcionar de verdade com `sh -c`, mesmo com espaço no caminho."""

    def comandos(self):
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(raiz, "docs", "HOOKS.md"), encoding="utf-8") as f:
            texto = f.read()
        blocos = re.findall(r"```json\n(.*?)```", texto, re.S)
        cfg = json.loads(blocos[0])
        return raiz, [h["command"] for ev in cfg["hooks"].values() for g in ev for h in g["hooks"]]

    def test_comando_do_json_roda_com_sh_c_e_registra_pulso(self):
        raiz, cmds = self.comandos()
        self.assertEqual(len(cmds), 2)
        casa = os.path.join(self.tmp, "casa")
        runtime = os.path.join(casa, "Library", "Application Support", "Orquestra", "runtime", "scripts")
        os.makedirs(runtime)
        os.symlink(os.path.join(raiz, "scripts", "hook-pulso.sh"), os.path.join(runtime, "hook-pulso.sh"))
        for cmd in cmds:
            self.assertIn('\\"', json.dumps(cmd))  # aspas escapadas no JSON
            r = subprocess.run(["sh", "-c", cmd], input=json.dumps({"session_id": "zz112233aa", "cwd": "/x/proj", "tool_name": "Bash"}),
                               capture_output=True, text=True, env=dict(os.environ, HOME=casa))
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), r.stderr)
        self.assertEqual(db.pulsos_recentes()[0]["agente"], "claude:zz112233")

    def test_sem_aspas_o_shell_quebraria_no_espaco(self):
        casa = os.path.join(self.tmp, "casa")
        cmd = "$HOME/Library/Application Support/Orquestra/runtime/scripts/hook-pulso.sh"
        r = subprocess.run(["sh", "-c", cmd], input="{}", capture_output=True, text=True, env=dict(os.environ, HOME=casa))
        self.assertNotEqual(r.returncode, 0)  # prova de que as aspas são necessárias


class TestBriefDiario(Base):
    def test_template_tem_todas_as_secoes_da_skill(self):
        t = brief.novo("Padronizar SKU", 2, "seller-turbo")
        v = brief.validar(t)
        self.assertEqual(v["secoes_ausentes"], [])
        self.assertGreater(v["por_preencher"], 3)
        self.assertFalse(v["ok"])
        self.assertIn("git grep", t)  # "todo teste que cita o que for mexido"
        self.assertIn("orq tarefa feita", t)

    def test_validar_brief_completo_e_comando(self):
        arq = self.escreve("b.md", brief.novo("x").replace("[PREENCHER", "pronto [x").replace("pronto [x:", "pronto:"))
        self.assertEqual(orq("brief", "validar", arq)[0], 0)
        self.assertEqual(orq("brief", "validar", self.escreve("ruim.md", "# só título\n"))[0], 1)
        saida = os.path.join(self.tmp, "novo.md")
        orq("brief", "novo", "--titulo", "Tarefa X", "--nivel", "1", "--saida", saida)
        self.assertIn("Tarefa X", open(saida).read())

    def test_diario_acrescenta_sem_apagar(self):
        arq = self.escreve("PASSAGEM-x.md", "# Passagem\n- antigo\n")
        orq("diario", "decidimos X", "--arquivo", arq)
        orq("diario", "feito Y", "--arquivo", arq)
        txt = open(arq).read()
        self.assertTrue(txt.startswith("# Passagem\n- antigo\n"))
        self.assertIn("decidimos X", txt)
        self.assertIn("feito Y", txt)
        self.assertEqual(len([e for e in db.eventos() if e["tipo"] == "diario"]), 2)


if __name__ == "__main__":
    unittest.main()
