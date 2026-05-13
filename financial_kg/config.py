from __future__ import annotations
import os
from pathlib import Path

# Load .env file if exists
_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE)
    except ImportError:
        # Manual fallback if python-dotenv not installed
        with open(_ENV_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")
DB_PATH = os.path.join(DATA_DIR, "tasks.db")

# ── LLM ─────────────────────────────────────────────────────────────────────
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

# ── Neo4j ────────────────────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# ── Parser ───────────────────────────────────────────────────────────────────
MAX_RANGE_EXPANSION = 2000


def save_config(
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    llm_model: str | None = None,
    neo4j_uri: str | None = None,
    neo4j_user: str | None = None,
    neo4j_password: str | None = None,
) -> None:
    """Save configuration back to .env file."""
    env_path = Path(__file__).parent.parent / ".env"

    # Read existing content
    existing = {}
    if env_path.exists():
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    existing[key.strip()] = val.strip()

    # Update with new values
    if llm_base_url is not None:
        existing["LLM_BASE_URL"] = llm_base_url
    if llm_api_key is not None:
        existing["LLM_API_KEY"] = llm_api_key
    if llm_model is not None:
        existing["LLM_MODEL"] = llm_model
    if neo4j_uri is not None:
        existing["NEO4J_URI"] = neo4j_uri
    if neo4j_user is not None:
        existing["NEO4J_USER"] = neo4j_user
    if neo4j_password is not None:
        existing["NEO4J_PASSWORD"] = neo4j_password

    # Write back
    lines = [
        "# ── LLM Configuration ──────────────────────────────────────────────────────────",
        f"LLM_BASE_URL={existing.get('LLM_BASE_URL', 'https://api.openai.com/v1')}",
        f"LLM_API_KEY={existing.get('LLM_API_KEY', '')}",
        f"LLM_MODEL={existing.get('LLM_MODEL', 'gpt-4o')}",
        "",
        "# ── Neo4j Configuration ─────────────────────────────────────────────────────────",
        f"NEO4J_URI={existing.get('NEO4J_URI', 'bolt://localhost:7687')}",
        f"NEO4J_USER={existing.get('NEO4J_USER', 'neo4j')}",
        f"NEO4J_PASSWORD={existing.get('NEO4J_PASSWORD', '')}",
    ]

    with open(env_path, "w", encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")

    # Update global vars
    global LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
    if llm_base_url is not None:
        LLM_BASE_URL = llm_base_url
    if llm_api_key is not None:
        LLM_API_KEY = llm_api_key
    if llm_model is not None:
        LLM_MODEL = llm_model
    if neo4j_uri is not None:
        NEO4J_URI = neo4j_uri
    if neo4j_user is not None:
        NEO4J_USER = neo4j_user
    if neo4j_password is not None:
        NEO4J_PASSWORD = neo4j_password