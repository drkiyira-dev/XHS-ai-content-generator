"""Static regression checks for the routed frontend image-upload contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_VUE = ROOT / "frontend" / "src" / "App.vue"
GENERATE_VIEW = ROOT / "frontend" / "src" / "views" / "GenerateView.vue"
WORKSPACE_STATE = ROOT / "frontend" / "src" / "state" / "workspace.ts"


def _record_keys(source: str, constant_name: str) -> set[str]:
    match = re.search(
        rf"const {constant_name}: Record<.*?> = \{{(.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return set(re.findall(r"['\"]([^'\"]+)['\"]\s*:", match.group(1)))


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"^(?P<indent>[ \t]*)(?:async\s+)?function {function_name}"
        rf"\([^)]*\)(?:\s*:\s*[^{{\n]+)?\s*\{{"
        rf"(?P<body>.*?)^(?P=indent)\}}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, function_name
    return match.group("body")


def test_frontend_accepts_every_backend_image_type() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    view_source = GENERATE_VIEW.read_text(encoding="utf-8")

    assert _record_keys(app_source, "FORMAT_BY_MIME") == {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
    assert _record_keys(app_source, "FORMAT_BY_EXTENSION") == {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".heic",
        ".heif",
    }
    assert 'accept=".jpg,.jpeg,.png,.webp,.heic,.heif"' in view_source
    assert "extensionFormat !== mimeFormat" in app_source
    assert "detectedFormat !== extensionFormat" in app_source


def test_frontend_heif_check_matches_the_backend_brand_boundary() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    view_source = GENERATE_VIEW.read_text(encoding="utf-8")

    assert "const HEADER_BYTES = 256" in app_source
    assert "readBrand(bytes, 4) !== 'ftyp'" in app_source
    assert {"heic", "heix", "heim", "heis", "mif1"} <= set(
        re.findall(r"['\"]([a-z0-9]{4})['\"]", app_source)
    )
    for rejected_brand in ("avif", "avis", "hevc", "hevx", "hevm", "hevs", "msf1"):
        assert rejected_brand in app_source
    assert "HEIC / HEIF" in view_source
    assert "浏览器不直接预览此格式，将由后端安全转换为 JPEG" in view_source


def test_heif_uses_an_accessible_nonvisual_placeholder_and_upload_discloses_data_flow() -> None:
    view_source = GENERATE_VIEW.read_text(encoding="utf-8")

    heif_start = view_source.index('v-if="imageIsHeif"')
    image_fallback = view_source.index("<img", heif_start)
    heif_branch = view_source[heif_start:image_fallback]

    assert 'class="heif-preview"' in heif_branch
    assert 'role="img"' in heif_branch
    assert 'aria-label="HEIC 或 HEIF 图片已选择"' in heif_branch
    assert "<Picture />" in heif_branch
    assert 'aria-hidden="true"' in heif_branch
    assert "浏览器不直接预览此格式，将由后端安全转换为 JPEG" in heif_branch

    assert 'aria-describedby="upload-help upload-privacy-help"' in view_source
    assert 'id="upload-help"' in view_source
    assert 'id="upload-privacy-help"' in view_source
    assert "图片会经本地后端发送至已配置的第三方视觉模型服务" in view_source
    assert "请勿上传敏感或无授权图片" in view_source


def test_upload_internal_queue_is_reset_through_the_workspace_epoch() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    view_source = GENERATE_VIEW.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_STATE.read_text(encoding="utf-8")
    clear_image = _function_body(app_source, "clearImage")

    assert "imageResetEpoch: Ref<number>" in workspace_source
    assert "imageResetEpoch.value += 1" in clear_image
    assert "imageResetEpoch," in app_source.split("provide(WORKSPACE_KEY", 1)[1]
    assert "watch(" in view_source
    assert "imageResetEpoch," in view_source
    assert "uploadRef.value?.clearFiles()" in view_source
    assert view_source.index("imageResetEpoch,") < view_source.index(
        "uploadRef.value?.clearFiles()"
    )


def test_frontend_documentation_describes_the_real_api_default() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    frontend_readme = (ROOT / "frontend" / "README.md").read_text(encoding="utf-8")
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "当前是 Mock" not in app_source
    assert "USE_MOCK = true" not in frontend_readme
    assert "VITE_USE_MOCK=true" in frontend_readme
    assert "当前版本只完成成员 B" not in root_readme
    assert "当前 `main` 已通过 PR 完成 A、B、C 三部分的整合" in root_readme
