"""Documentation must not contradict the code.

Findings #9 M4/K4/K5: the README and info.md claimed things the code does
not do (controls disabled by default, missing restart, stale roadmap). Each
test here fails when the docs drift away from the implementation again.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")
INFO = (REPO / "info.md").read_text(encoding="utf-8")
COMPONENT = REPO / "custom_components" / "powerline"


def _component_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(COMPONENT.rglob("*.py"))
    )


def test_control_entity_docs_match_enabled_by_default():
    """(#9 M4) The LED / Power Saving / QoS controls are created enabled —
    no file sets ``_attr_entity_registry_enabled_default = False`` — so the
    docs must not claim they are disabled by default."""
    if "_attr_entity_registry_enabled_default = False" in _component_sources():
        return  # entities disabled by default: the old docs were fine

    for name in ("LED", "Power Saving", "QoS Priority"):
        for line in (README + INFO).splitlines():
            if line.strip().startswith(f"| {name} "):
                assert "Disabled" not in line, (
                    f"docs mark '{name}' as disabled by default: {line.strip()}"
                )
    for doc, where in ((README, "README.md"), (INFO, "info.md")):
        assert "are disabled by default" not in doc, (
            f"{where} claims the controls are disabled by default"
        )


def test_fritz_restart_documented_as_available():
    """(#9 K4) The FRITZ restart button exists (RestartButton, VS_RS_DEV),
    so the docs must not claim restart is not implemented — only the
    factory reset is still missing (pending a capture)."""
    assert "VS_RS_DEV" in _component_sources()  # sanity: the path exists
    assert "restart/reset are not implemented yet" not in README
