import json
import os
import time
import unittest
from unittest import mock

from orquestra import coletores
from tests.base import Base

LIMITE = '{"type":"event_msg","payload":{"rate_limits":{"limit_id":"codex","primary":{"used_percent":83.0,"window_minutes":10080,"resets_at":1794384386},"secondary":null}}}\n'


class TestCodex(Base):
    def test_cota_pelo_ultimo_rate_limits(self):
        velho = LIMITE.replace("83.0", "10.0")
        self.escreve("codex/sessions/2026/11/07/rollout-a.jsonl", velho + LIMITE)
        c = coletores.codex_cota()
        self.assertEqual(c["usado_pct"], 83.0)
        self.assertEqual(c["janela_min"], 10080)
        self.assertEqual(c["renova_em"], 1794384386)

    def test_sem_sessoes(self):
        self.assertIsNone(coletores.codex_cota())

    def test_processos_e_titulo(self):
        self.escreve("logs/T477.log", "x")
        ps = ('  101   02:19:27 /Users/x/.local/bin/codex exec -m gpt-6-sol --sandbox s -C /a/b/seller-turbo-t477 '
              '# T477 — Diagnóstico de produto\\012\\012Skills...\n'
              '  102   00:05 /usr/bin/vim codex exec nao\n')
        p = coletores.codex_processos(ps)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["tarefa"], "T477")
        self.assertTrue(p[0]["titulo"].startswith("Diagnóstico de produto"))
        self.assertEqual(p[0]["ha_segundos"], 2 * 3600 + 19 * 60 + 27)
        self.assertEqual(p[0]["modelo"], "gpt-6-sol")
        self.assertLess(p[0]["idade_log"], 5)

    def test_auth_json_nunca_e_lido(self):
        self.escreve("codex/auth.json", '{"tokens":{"access_token":"SEGREDO"}}')
        self.escreve("codex/sessions/2026/11/07/rollout-a.jsonl", LIMITE)
        self.escreve("claude/projects/p/auth.json", "SEGREDO")
        self.escreve("claude/projects/p/s1.jsonl", "")
        abertos = []
        real = open

        def espia(f, *a, **k):
            abertos.append(str(f))
            return real(f, *a, **k)

        with mock.patch("builtins.open", espia):
            coletores.codex_cota()
            coletores.claude_sessoes()
            coletores.tarefas_agendadas()
        self.assertFalse([a for a in abertos if os.path.basename(a) in ("auth.json", ".env")], abertos)
        with self.assertRaises(PermissionError):
            coletores._abrir(os.path.join(self.tmp, "codex", "auth.json"))
        with self.assertRaises(PermissionError):
            coletores._abrir("/x/.env")


def linha(msg_id, modelo, i, o, ts, cwd="/p/meu-projeto"):
    return json.dumps({"cwd": cwd, "timestamp": ts, "message": {"id": msg_id, "model": modelo,
                       "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": 5}}}) + "\n"


class TestClaude(Base):
    def test_tokens_por_modelo_e_dia_com_dedup(self):
        hoje = time.strftime("%Y-%m-%dT12:00:00-03:00")
        conteudo = (linha("m1", "claude-sonnet-5-5", 10, 1, hoje) + linha("m1", "claude-sonnet-5-5", 10, 50, hoje)
                    + linha("m2", "claude-haiku-5-5", 3, 7, hoje) + "lixo não-json\n")
        self.escreve("claude/projects/-x/sessao1.jsonl", conteudo)
        s = coletores.claude_sessoes()
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["projeto"], "meu-projeto")
        tot = coletores.tokens_por_modelo(s, dia=time.strftime("%Y-%m-%d", time.localtime(time.mktime(time.strptime(hoje[:19], "%Y-%m-%dT%H:%M:%S")))))
        self.assertEqual(tot["claude-sonnet-5-5"]["saida"], 50)  # a linha repetida não soma duas vezes
        self.assertEqual(tot["claude-haiku-5-5"]["entrada"], 3)

    def test_ignora_transcript_antigo(self):
        p = self.escreve("claude/projects/-x/velho.jsonl", linha("m", "claude-sonnet-5-5", 1, 1, "2026-01-01T00:00:00Z"))
        os.utime(p, (time.time() - 3 * 86400,) * 2)
        self.assertEqual(coletores.claude_sessoes(), [])

    def test_tarefas_agendadas(self):
        self.escreve("claude/scheduled-tasks/t1/SKILL.md", "---\nname: t1\ndescription: Faz X às 3h\n---\ncorpo")
        self.assertEqual(coletores.tarefas_agendadas()[0]["descricao"], "Faz X às 3h")

    def test_coletar_tudo_grava_cota(self):
        self.escreve("codex/sessions/2026/11/07/rollout-a.jsonl", LIMITE)
        d = coletores.coletar_tudo()
        self.assertEqual(d["erros"], [])
        from orquestra import db
        self.assertEqual(db.cotas()[0]["agente"], "codex")


if __name__ == "__main__":
    unittest.main()
