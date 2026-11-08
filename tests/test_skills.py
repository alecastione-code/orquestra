import os
import unittest

from orquestra import skills
from tests.base import Base

FM = "---\nname: %s\ndescription: >\n  Faz coisas boas com dados. Use quando o dono pedir\n  relatório de vendas.\n---\ncorpo\n"


class TestSkills(Base):
    def setUp(self):
        super().setUp()
        self.alvo = os.path.join(self.tmp, "agents", "bonita")
        self.escreve("agents/bonita/SKILL.md", FM % "bonita")
        os.symlink(self.alvo, os.path.join(self.env["ORQUESTRA_SKILLS"], "bonita"))

    def test_lista_e_efeitos(self):
        s = skills.listar()[0]
        self.assertTrue(s["ativa"])
        self.assertIn("Faz coisas boas com dados.", s["descricao"])
        self.assertIn("Use quando o dono pedir relatório de vendas", s["se_ativa"])
        self.assertTrue(s["se_desativada"].startswith("Desativada:"))

    def test_desativar_e_ativar_move_o_link_sem_apagar(self):
        skills.desativar("bonita")
        off = os.path.join(self.env["ORQUESTRA_SKILLS_OFF"], "bonita")
        self.assertTrue(os.path.islink(off))
        self.assertFalse(os.path.lexists(os.path.join(self.env["ORQUESTRA_SKILLS"], "bonita")))
        self.assertTrue(os.path.isfile(os.path.join(self.alvo, "SKILL.md")))  # conteúdo intacto
        self.assertFalse(skills.listar()[0]["ativa"])
        skills.ativar("bonita")
        self.assertTrue(skills.listar()[0]["ativa"])

    def test_nome_malicioso_e_inexistente(self):
        for nome in ("../x", "a/b", "", ".oculta"):
            with self.assertRaises(ValueError):
                skills.desativar(nome)
        with self.assertRaises(KeyError):
            skills.desativar("nao-existe")
        with self.assertRaises(KeyError):
            skills.ativar("bonita")  # já ativa


if __name__ == "__main__":
    unittest.main()
