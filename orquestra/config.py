"""Caminhos e constantes. Tudo lido do ambiente a cada chamada (os testes trocam por pastas temporárias)."""
import os

PORTA = 4747
HOST = "127.0.0.1"
INTERVALO_COLETA = 60

# Nunca lidos pelos coletores, em qualquer pasta.
ARQUIVOS_PROIBIDOS = ("auth.json", ".env", "credentials.json", "token", "tokens.json", "id_rsa", "id_ed25519")


def _p(var, padrao):
    return os.path.expanduser(os.environ.get(var) or padrao)


def casa_orquestra():
    return _p("ORQUESTRA_HOME", "~/Library/Application Support/Orquestra")


def caminho_db():
    return _p("ORQUESTRA_DB", os.path.join(casa_orquestra(), "orquestra.db"))


def codex_home():
    return _p("ORQUESTRA_CODEX_HOME", "~/.codex")


def claude_home():
    return _p("ORQUESTRA_CLAUDE_HOME", "~/.claude")


def skills_ativas():
    return _p("ORQUESTRA_SKILLS", os.path.join(claude_home(), "skills"))


def skills_desativadas():
    return _p("ORQUESTRA_SKILLS_OFF", os.path.join(claude_home(), "skills-desativadas"))


def pasta_logs_codex():
    return _p(
        "ORQUESTRA_LOGS_CODEX",
        "~/Documents/Codex/2026-10-15/files-pasted-by-the-user-voc/outputs/claude-lote-consertos-20261018",
    )


def pasta_web():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def pasta_vigia():
    return _p("ORQUESTRA_VIGIA_DIR", os.path.join(pasta_logs_codex(), "vigia"))


def repo_seller_turbo():
    return os.environ.get("ORQUESTRA_REPO_GH", "alecastione-code/seller-turbo-painel")


def sessoes_app():
    """Metadados das sessões do app Claude (só título e id são lidos)."""
    return _p("ORQUESTRA_APP_SESSIONS", "~/Library/Application Support/Claude/claude-code-sessions")


def banco_de_producao():
    return os.path.expanduser("~/Library/Application Support/Orquestra/orquestra.db")
