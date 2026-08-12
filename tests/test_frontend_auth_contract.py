"""Static service-layer contracts for the opt-in frontend account flow."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "frontend" / "src" / "services"
API_SERVICE = SERVICES / "api.ts"
AUTH_SERVICE = SERVICES / "auth.ts"
GENERATION_SERVICE = SERVICES / "generation.ts"


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"(?:export\s+)?async function {function_name}"
        rf"(?:<[^>]+>)?\([^)]*\).*?\{{(.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, function_name
    return match.group(1)


def test_shared_api_enables_cookie_and_csrf_only_in_explicit_auth_mode() -> None:
    source = API_SERVICE.read_text(encoding="utf-8")
    request_body = _function_body(source, "requestApi")

    assert (
        "export const AUTH_ENABLED = "
        "import.meta.env.VITE_AUTH_ENABLED === 'true'"
    ) in source
    assert "if (AUTH_ENABLED)" in request_body
    assert "init.credentials = 'include'" in request_body
    assert "if (options.csrf)" in request_body
    assert "headers.set(CSRF_HEADER_NAME, CSRF_HEADER_VALUE)" in request_body
    assert request_body.index("if (AUTH_ENABLED)") < request_body.index(
        "init.credentials = 'include'"
    ) < request_body.index("headers.set(CSRF_HEADER_NAME, CSRF_HEADER_VALUE)")
    assert "credentials: 'include'" not in source


def test_auth_service_exposes_only_cookie_based_account_operations() -> None:
    source = AUTH_SERVICE.read_text(encoding="utf-8")

    for public_contract in (
        "export interface AuthUser",
        "export class AuthError extends Error",
        "export async function restoreSession(): Promise<AuthUser | null>",
        "export async function registerAccount(",
        "export async function loginAccount(",
        "export async function logoutAccount(): Promise<void>",
    ):
        assert public_contract in source

    for endpoint in (
        "/api/v1/auth/me",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
    ):
        assert endpoint in source

    restore = _function_body(source, "restoreSession")
    logout = _function_body(source, "logoutAccount")
    submit = _function_body(source, "submitCredentials")
    assert "if (!AUTH_ENABLED)" in restore
    assert "method: 'GET'" in restore
    assert "cache: 'no-store'" in restore
    assert "error.code === 'AUTH_REQUIRED'" in restore
    assert "return null" in restore
    assert "method: 'POST'" in submit
    assert "csrf: true" in submit
    assert "method: 'POST'" in logout
    assert "csrf: true" in logout
    assert "expectNoContent: true" in logout
    assert ".json(" not in logout


def test_generation_service_uses_shared_auth_ready_transport() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")
    generate = _function_body(source, "realGenerate")
    history = _function_body(source, "realListGenerations")

    assert "from './api'" in source
    assert "fetch(" not in source
    assert "requestApi<unknown>" in generate
    assert "parseGenerationResponse(response)" in generate
    assert "method: 'POST'" in generate
    assert "csrf: true" in generate
    assert "'Content-Type':" not in generate
    assert "requestApi<unknown>" in history
    assert "parseHistoryResponse(response)" in history
    assert "method: 'GET'" in history
    assert "cache: 'no-store'" in history
    assert "user_id" not in generate
    assert "user_id" not in history


def test_frontend_services_never_store_tokens_or_trust_server_error_messages() -> None:
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in SERVICES.glob("*.ts")
    }
    combined = "\n".join(sources.values())

    for forbidden_storage in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "cookieStore",
    ):
        assert forbidden_storage not in combined

    auth_source = sources[AUTH_SERVICE]
    assert "mockCurrentUser" in auth_source
    assert "mockCurrentUser: AuthUser | null" in auth_source
    assert not re.search(r"(?:raw_)?token\s*:", auth_source, flags=re.IGNORECASE)

    api_source = sources[API_SERVICE]
    assert "message?: unknown" not in api_source
    assert "envelope?.error?.message" not in api_source
    assert "candidate in SAFE_ERRORS" in api_source
    assert "options.networkMessage" in api_source
    assert "error.message ||" not in combined
    assert "FORM_FIELD_TOO_LARGE:" in api_source
    assert "INVALID_IMAGE_DIMENSIONS:" in api_source


def test_mock_account_history_is_partitioned_by_mock_identity() -> None:
    auth_source = AUTH_SERVICE.read_text(encoding="utf-8")
    generation_source = GENERATION_SERVICE.read_text(encoding="utf-8")

    assert "const mockUserIds = new Map<string, number>()" in auth_source
    assert "export function getMockAccountId(): number | null" in auth_source
    assert "mockUserIds.get(normalizedEmail)" in auth_source
    assert "mockUserIds.set(normalizedEmail, userId)" in auth_source

    assert (
        "const mockHistoryByOwner = new Map<number, GenerationHistoryItem[]>()"
        in generation_source
    )
    assert "function mockOwnerId(): number" in generation_source
    assert "mockHistoryFor(mockOwnerId())" in generation_source
    assert "const mockHistory: GenerationResponse[]" not in generation_source


def test_generation_payloads_are_validated_before_the_page_receives_them() -> None:
    source = GENERATION_SERVICE.read_text(encoding="utf-8")

    for required_field in (
        "value.generation_id",
        "value.image_summary",
        "value.title",
        "value.body",
        "value.created_at",
    ):
        assert f"isBoundedString({required_field}" in source
    assert "Array.isArray(value.tags)" in source
    assert "value.tags.every(tag => isBoundedString(tag" in source
    assert "value.tags.length < 3" in source
    assert "value.tags.length > 5" in source
    assert "Array.isArray(value.items)" in source
    assert "value.items.length > 50" in source
    assert "Number.isInteger(value.count)" in source
    assert "value.count !== value.items.length" in source
    assert "value.items.map(item => parseHistoryItem(item))" in source
    assert "typeof value.has_image_preview !== 'boolean'" in source
    assert "parseImagePreviewUrl(" in source
    assert "'INVALID_RESPONSE'" in source
