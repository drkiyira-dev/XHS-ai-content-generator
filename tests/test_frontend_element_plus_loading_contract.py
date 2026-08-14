"""Keep Element Plus imports bounded to the components the app actually uses."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_main_does_not_install_the_full_element_plus_plugin_or_icon_catalog() -> None:
    source = (FRONTEND / "src/main.ts").read_text(encoding="utf-8")

    assert "app.use(ElementPlus)" not in source
    assert "ElementPlusIconsVue" not in source
    assert "element-plus/dist/index.css" not in source
    assert "import ElementPlus from 'element-plus'" not in source


def test_main_imports_only_the_required_component_and_service_styles() -> None:
    source = (FRONTEND / "src/main.ts").read_text(encoding="utf-8")
    required = {
        "alert",
        "button",
        "dialog",
        "empty",
        "icon",
        "input",
        "message",
        "message-box",
        "radio-button",
        "radio-group",
        "skeleton",
        "switch",
        "tag",
        "upload",
    }

    for component in required:
        assert f"element-plus/es/components/{component}/style/css" in source


def test_each_view_explicitly_imports_its_element_plus_components() -> None:
    contracts = {
        "src/App.vue": ("ElButton",),
        "src/components/AuthDialog.vue": (
            "ElAlert",
            "ElButton",
            "ElDialog",
            "ElInput",
        ),
        "src/views/GenerateView.vue": (
            "ElAlert",
            "ElButton",
            "ElIcon",
            "ElInput",
            "ElRadioButton",
            "ElRadioGroup",
            "ElSwitch",
            "ElTag",
            "ElUpload",
            "UploadFilled",
        ),
        "src/views/HistoryView.vue": (
            "ElAlert",
            "ElButton",
            "ElEmpty",
            "ElSkeleton",
            "ElTag",
        ),
    }

    for relative_path, names in contracts.items():
        source = (FRONTEND / relative_path).read_text(encoding="utf-8")
        for name in names:
            assert name in source
