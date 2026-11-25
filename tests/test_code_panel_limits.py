from src.code_tools import code_interpreter_panel as panel


def test_large_text_below_original_five_megabyte_threshold_has_bounded_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "WORKSPACE", tmp_path)
    file = tmp_path / "large.txt"
    file.write_text("Beginning\n" + "0123456789" * 30_000 + "\nEnding\n", encoding="utf-8")

    preview = panel._display_file(file.name)
    assert "TRUNCATED" in preview
    assert "Beginning" in preview and "Ending" in preview
    assert len(preview) < 50_000


def test_utf8_preview_survives_a_split_multibyte_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "WORKSPACE", tmp_path)
    monkeypatch.setattr(panel, "MAX_FILE_READ_SIZE", 12)
    monkeypatch.setattr(panel, "_TRUNC_HEAD", 8)
    monkeypatch.setattr(panel, "_TRUNC_TAIL", 2)
    file = tmp_path / "unicode.log"
    file.write_text("prefix\n" + "中文" * 10 + "x", encoding="utf-8")

    preview = panel._display_file(file.name)
    assert "TRUNCATED" in preview
    assert "Error reading" not in preview
    assert "prefix" in preview and "x\n--- END TRUNCATED ---" in preview


def test_many_open_files_do_not_put_unbounded_text_into_a_request(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "WORKSPACE", tmp_path)
    paths = {f"code{index}.txt" for index in range(7)}
    for path in paths:
        (tmp_path / path).write_text("def fixture():\n" * 1900, encoding="utf-8")

    rendered = panel.CodeInterpreterPanel().render(paths)

    assert "def fixture():" in rendered
    assert "omitted" in rendered
    assert len(rendered) < panel.CODE_PANEL_WARN_CHARS + 6_000
