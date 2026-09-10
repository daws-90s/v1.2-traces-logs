from app.slack.formatting import markdown_to_mrkdwn


def test_headings_become_bold_no_hashes():
    out = markdown_to_mrkdwn("# Incident RCA\n## Root Cause\ntext")
    assert "#" not in out
    assert "*Incident RCA*" in out
    assert "*Root Cause*" in out


def test_bold_uses_single_asterisk():
    out = markdown_to_mrkdwn("- **MySQL saturation** — rejected: no evidence")
    assert "**" not in out
    assert "*MySQL saturation*" in out


def test_bullets_get_real_glyph():
    out = markdown_to_mrkdwn("- first\n- second")
    assert "• first" in out
    assert "• second" in out
    assert "- " not in out


def test_links_become_slack_link_syntax():
    out = markdown_to_mrkdwn("See [the dashboard](https://grafana.example.com/d/abc) for more.")
    assert "<https://grafana.example.com/d/abc|the dashboard>" in out


def test_plain_text_unchanged():
    assert markdown_to_mrkdwn("plain line, no markdown here") == "plain line, no markdown here"
