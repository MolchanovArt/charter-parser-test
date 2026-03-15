from charter_parser.config import load_settings


def test_load_settings_defaults():
    settings = load_settings("configs/default.yaml")
    assert settings.project.pdf_path.endswith("voyage-charter-example.pdf")
    assert settings.parsing.source_of_truth == "lines"
