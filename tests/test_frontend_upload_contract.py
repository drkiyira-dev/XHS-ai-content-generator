"""Static regression checks for the frontend image-upload contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP_VUE = ROOT / "frontend" / "src" / "App.vue"


def _record_keys(source: str, constant_name: str) -> set[str]:
    match = re.search(
        rf"const {constant_name}: Record<.*?> = \{{(.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None
    return set(re.findall(r"['\"]([^'\"]+)['\"]\s*:", match.group(1)))


def test_frontend_accepts_every_backend_image_type() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    assert _record_keys(source, "FORMAT_BY_MIME") == {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    }
    assert _record_keys(source, "FORMAT_BY_EXTENSION") == {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".heic",
        ".heif",
    }
    assert 'accept=".jpg,.jpeg,.png,.webp,.heic,.heif"' in source
    assert "extensionFormat !== mimeFormat" in source
    assert "detectedFormat !== extensionFormat" in source


def test_frontend_heif_check_matches_the_backend_brand_boundary() -> None:
    source = APP_VUE.read_text(encoding="utf-8")

    assert "const HEADER_BYTES = 256" in source
    assert "readBrand(bytes, 4) !== 'ftyp'" in source
    assert {"heic", "heix", "heim", "heis", "mif1"} <= set(
        re.findall(r"['\"]([a-z0-9]{4})['\"]", source)
    )
    for rejected_brand in ("avif", "avis", "hevc", "hevx", "hevm", "hevs", "msf1"):
        assert rejected_brand in source
    assert "HEIC / HEIF" in source
    assert "浏览器不直接预览此格式，将由后端安全转换为 JPEG" in source


def test_frontend_documentation_describes_the_real_api_default() -> None:
    app_source = APP_VUE.read_text(encoding="utf-8")
    frontend_readme = (ROOT / "frontend" / "README.md").read_text(encoding="utf-8")
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "当前是 Mock" not in app_source
    assert "USE_MOCK = true" not in frontend_readme
    assert "VITE_USE_MOCK=true" in frontend_readme
    assert "当前版本只完成成员 B" not in root_readme
    assert "当前 `main` 已通过 PR 完成 A、B、C 三部分的整合" in root_readme
