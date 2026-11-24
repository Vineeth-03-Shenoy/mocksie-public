from pathlib import Path

CONSENT = (Path(__file__).resolve().parent.parent / "backend" / "content" / "consent.md").read_text(encoding="utf-8")


def test_consent_discloses_everything_the_design_requires():
    text = CONSENT.lower()
    assert "openai" in text and "improve its models" in text          # free allowance shares data for training
    assert "no audio is recorded" in text
    assert "90 days" in text                                          # retention
    assert "leaving full screen" in text and "ends the interview" in text
    assert "contact details are removed" in text
