from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_env_example_contains_placeholders_without_real_identifiers():
    content = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "DISCORD_APPLICATION_ID=" in content
    assert "DISCORD_PUBLIC_KEY=" in content
    assert "DISCORD_BOT_TOKEN=" in content
    assert "1552123238543523910" not in content
    assert "be400902" not in content


def test_discord_dependency_is_optional_from_core_requirements():
    core = (ROOT / "requirements-v2-dev.txt").read_text(encoding="utf-8")
    optional = (ROOT / "requirements-discord.txt").read_text(encoding="utf-8")

    assert "discord.py" not in core
    assert "discord.py" in optional
