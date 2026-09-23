from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_env_example_contains_placeholders_without_real_identifiers():
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in content.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }

    assert "DISCORD_APPLICATION_ID=" in content
    assert "DISCORD_PUBLIC_KEY=" in content
    assert "DISCORD_BOT_TOKEN=" in content
    assert values["DISCORD_APPLICATION_ID"] == ""
    assert values["DISCORD_PUBLIC_KEY"] == ""
    assert values["DISCORD_BOT_TOKEN"] == ""


def test_discord_dependency_is_optional_from_core_requirements():
    core = (ROOT / "requirements-v2-dev.txt").read_text(encoding="utf-8")
    optional = (ROOT / "requirements-discord.txt").read_text(encoding="utf-8")

    assert "discord.py" not in core
    assert "discord.py" in optional
