"""SQLite integration tests for the HTTP-independent authentication core."""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock
from typing import Any

from anyio import CapacityLimiter
import pytest
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.db import AuthSession, Base, User
from backend.db import auth_repository
from backend.services.auth import (
    AuthService,
    AuthUnavailable,
    EmailUnavailable,
    InvalidCredentials,
    SQLAlchemyAuthStore,
)
from backend.services.auth.service import DUMMY_PASSWORD_HASH


VALID_PASSWORD = "correct horse battery staple"
TOKEN_A = "A" * 43
TOKEN_B = "B" * 43
TOKEN_C = "C" * 43


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FastHasher:
    """Fast deterministic test double with optional verification side effects."""

    def __init__(self, *, verify_hook: Callable[[], None] | None = None) -> None:
        self.verify_hook = verify_hook
        self.verify_calls: list[tuple[str, str]] = []
        self.return_updated_hash = False

    def hash(self, password: str) -> str:
        return "test$" + sha256(password.encode("utf-8")).hexdigest()

    def verify_and_update(
        self,
        password: str,
        password_hash: str,
    ) -> tuple[bool, str | None]:
        self.verify_calls.append((password, password_hash))
        if self.verify_hook is not None:
            self.verify_hook()
        valid = password_hash == self.hash(password)
        if valid and self.return_updated_hash:
            return True, "updated$" + password_hash
        return valid, None


class SequenceTokenFactory:
    def __init__(self, *tokens: str) -> None:
        self._tokens = iter(tokens)

    def __call__(self) -> str:
        return next(self._tokens)


class BlockingHasher(FastHasher):
    """Record password-worker concurrency while workers wait for release."""

    def __init__(self) -> None:
        super().__init__()
        self.release = Event()
        self.two_started = Event()
        self._lock = Lock()
        self.started = 0
        self.active = 0
        self.maximum_active = 0

    def hash(self, password: str) -> str:
        with self._lock:
            self.started += 1
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            if self.started >= 2:
                self.two_started.set()
        self.release.wait(timeout=3)
        try:
            return super().hash(password)
        finally:
            with self._lock:
                self.active -= 1


class FailingVerifyHasher(FastHasher):
    def __init__(self, private_detail: str) -> None:
        super().__init__()
        self.private_detail = private_detail

    def verify_and_update(
        self,
        password: str,
        password_hash: str,
    ) -> tuple[bool, str | None]:
        raise RuntimeError(self.private_detail)


@contextmanager
def auth_store(
    tmp_path: Path,
) -> Iterator[tuple[SQLAlchemyAuthStore, sessionmaker[Session]]]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth-service.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: Any, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )
    store = SQLAlchemyAuthStore(factory, limiter=CapacityLimiter(4))
    try:
        yield store, factory
    finally:
        engine.dispose()


def build_service(
    store: SQLAlchemyAuthStore,
    *,
    clock: MutableClock,
    hasher: FastHasher | None = None,
    token_factory: Callable[[], str] | None = None,
) -> AuthService:
    return AuthService(
        store,
        password_hasher=hasher or FastHasher(),
        password_limiter=CapacityLimiter(2),
        clock=clock,
        token_factory=token_factory or SequenceTokenFactory(TOKEN_A, TOKEN_B, TOKEN_C),
    )


def test_registration_is_atomic_and_stores_no_raw_secret(tmp_path: Path) -> None:
    now = datetime(2026, 8, 12, 4, 0, tzinfo=UTC)
    clock = MutableClock(now)

    with auth_store(tmp_path) as (store, factory):
        service = build_service(store, clock=clock)
        issued = asyncio.run(
            service.register(
                email=" Demo@Example.COM ",
                password=VALID_PASSWORD,
            )
        )

        with factory() as session:
            user = session.execute(select(User)).scalar_one()
            login_session = session.execute(select(AuthSession)).scalar_one()

    assert issued.user.user_id == user.id
    assert issued.user.email == "demo@example.com"
    assert issued.user.email_verified is False
    assert issued.token == TOKEN_A
    assert issued.expires_at == now + timedelta(days=7)
    assert user.email_verified_at is None
    assert user.password_hash != VALID_PASSWORD
    assert VALID_PASSWORD not in user.password_hash
    assert login_session.token_hash == sha256(TOKEN_A.encode("ascii")).digest()
    assert TOKEN_A.encode() not in login_session.token_hash
    assert TOKEN_A not in repr(issued)


def test_duplicate_email_leaves_exactly_one_user_and_session(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        first = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        second = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_B),
        )

        async def register_both() -> tuple[object, object]:
            return tuple(
                await asyncio.gather(
                    first.register(email="demo@example.com", password=VALID_PASSWORD),
                    second.register(email="DEMO@example.com", password=VALID_PASSWORD),
                    return_exceptions=True,
                )
            )  # type: ignore[return-value]

        results = asyncio.run(register_both())
        with factory() as session:
            user_count = session.scalar(select(func.count()).select_from(User))
            session_count = session.scalar(
                select(func.count()).select_from(AuthSession)
            )

    assert sum(isinstance(result, EmailUnavailable) for result in results) == 1
    duplicate = next(
        result for result in results if isinstance(result, EmailUnavailable)
    )
    assert duplicate.__cause__ is None
    assert duplicate.__context__ is None
    assert user_count == session_count == 1


def test_service_cleans_a_store_duplicate_email_exception_chain() -> None:
    private_detail = "PRIVATE DUPLICATE CLASSIFICATION DETAIL"

    class LeakyDuplicateStore:
        async def register_with_session(self, **_kwargs: object) -> object:
            try:
                raise RuntimeError(private_detail)
            except RuntimeError as error:
                raise EmailUnavailable() from error

    service = AuthService(
        LeakyDuplicateStore(),  # type: ignore[arg-type]
        password_hasher=FastHasher(),
        password_limiter=CapacityLimiter(1),
        clock=MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC)),
        token_factory=SequenceTokenFactory(TOKEN_A),
    )

    with pytest.raises(EmailUnavailable) as caught:
        asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_detail not in str(caught.value)
    assert private_detail not in repr(caught.value)


def test_registration_retries_a_token_collision_without_leaving_partial_user(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        first = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        asyncio.run(first.register(email="first@example.com", password=VALID_PASSWORD))

        second = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A, TOKEN_B),
        )
        issued = asyncio.run(
            second.register(email="second@example.com", password=VALID_PASSWORD)
        )

        with factory() as session:
            users = session.execute(select(User).order_by(User.id)).scalars().all()
            sessions = session.execute(
                select(AuthSession).order_by(AuthSession.id)
            ).scalars().all()

    assert issued.token == TOKEN_B
    assert [user.email for user in users] == [
        "first@example.com",
        "second@example.com",
    ]
    assert len(sessions) == 2


def test_registration_exhausts_token_collisions_without_partial_account(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        for index, token in enumerate((TOKEN_A, TOKEN_B, TOKEN_C), start=1):
            service = build_service(
                store,
                clock=clock,
                token_factory=SequenceTokenFactory(token),
            )
            asyncio.run(
                service.register(
                    email=f"seed{index}@example.com",
                    password=VALID_PASSWORD,
                )
            )

        colliding = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A, TOKEN_B, TOKEN_C),
        )
        with pytest.raises(AuthUnavailable):
            asyncio.run(
                colliding.register(
                    email="target@example.com",
                    password=VALID_PASSWORD,
                )
            )

        with factory() as session:
            emails = session.execute(select(User.email).order_by(User.id)).scalars().all()
            session_count = session.scalar(
                select(func.count()).select_from(AuthSession)
            )

    assert "target@example.com" not in emails
    assert len(emails) == session_count == 3


def test_password_hashing_runs_off_loop_with_a_global_capacity_limit(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))
    hasher = BlockingHasher()

    with auth_store(tmp_path) as (store, _factory):
        service = AuthService(
            store,
            password_hasher=hasher,
            password_limiter=CapacityLimiter(2),
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A, TOKEN_B, TOKEN_C),
        )

        async def register_three() -> None:
            tasks = [
                asyncio.create_task(
                    service.register(
                        email=f"worker{index}@example.com",
                        password=VALID_PASSWORD,
                    )
                )
                for index in range(3)
            ]

            async def wait_for_two_workers() -> None:
                while not hasher.two_started.is_set():
                    await asyncio.sleep(0.001)

            await asyncio.wait_for(wait_for_two_workers(), timeout=2)
            assert hasher.started == 2
            assert hasher.maximum_active == 2
            assert not any(task.done() for task in tasks)
            hasher.release.set()
            await asyncio.gather(*tasks)

        asyncio.run(register_three())

    assert hasher.started == 3
    assert hasher.maximum_active == 2


def test_login_uses_dummy_verify_and_one_fixed_error_for_unknown_account(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))
    hasher = FastHasher()

    with auth_store(tmp_path) as (store, _factory):
        service = build_service(store, clock=clock, hasher=hasher)
        with pytest.raises(InvalidCredentials) as caught:
            asyncio.run(
                service.login(email="missing@example.com", password=VALID_PASSWORD)
            )

    assert hasher.verify_calls == [(VALID_PASSWORD, DUMMY_PASSWORD_HASH)]
    assert "missing@example.com" not in str(caught.value)
    assert VALID_PASSWORD not in str(caught.value)


def test_password_provider_failure_becomes_fixed_invalid_credentials(
    tmp_path: Path,
) -> None:
    private_detail = "PRIVATE ARGON PROVIDER DETAIL"
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, _factory):
        register_service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            register_service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        login_service = build_service(
            store,
            clock=clock,
            hasher=FailingVerifyHasher(private_detail),
            token_factory=SequenceTokenFactory(TOKEN_B),
        )
        with pytest.raises(InvalidCredentials) as caught:
            asyncio.run(
                login_service.login(
                    email=issued.user.email,
                    password=VALID_PASSWORD,
                )
            )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_detail not in str(caught.value)
    assert private_detail not in repr(caught.value)


def test_login_creates_new_session_and_updates_hash_in_one_transaction(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))
    hasher = FastHasher()

    with auth_store(tmp_path) as (store, factory):
        register_service = build_service(
            store,
            clock=clock,
            hasher=hasher,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        first = asyncio.run(
            register_service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        hasher.return_updated_hash = True
        clock.now += timedelta(hours=1)
        login_service = build_service(
            store,
            clock=clock,
            hasher=hasher,
            token_factory=SequenceTokenFactory(TOKEN_B),
        )
        second = asyncio.run(
            login_service.login(email="DEMO@example.com", password=VALID_PASSWORD)
        )

        with factory() as session:
            user = session.get(User, first.user.user_id)
            sessions = session.execute(select(AuthSession)).scalars().all()

    assert second.user == first.user
    assert second.token == TOKEN_B
    assert user is not None
    assert user.password_hash.startswith("updated$test$")
    assert user.last_login_at == clock.now.replace(tzinfo=None)
    assert len(sessions) == 2


def test_login_rejects_password_or_active_state_change_during_verify(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        initial_hasher = FastHasher()
        register_service = build_service(
            store,
            clock=clock,
            hasher=initial_hasher,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            register_service.register(email="demo@example.com", password=VALID_PASSWORD)
        )

        def deactivate_during_verify() -> None:
            with factory.begin() as session:
                session.execute(
                    update(User)
                    .where(User.id == issued.user.user_id)
                    .values(is_active=False)
                )

        racing_hasher = FastHasher(verify_hook=deactivate_during_verify)
        service = build_service(
            store,
            clock=clock,
            hasher=racing_hasher,
            token_factory=SequenceTokenFactory(TOKEN_B),
        )
        with pytest.raises(InvalidCredentials):
            asyncio.run(service.login(email=issued.user.email, password=VALID_PASSWORD))

        with factory() as session:
            session_count = session.scalar(
                select(func.count()).select_from(AuthSession)
            )

    assert session_count == 1


def test_login_rejects_password_hash_change_during_verify(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        register_service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            register_service.register(email="demo@example.com", password=VALID_PASSWORD)
        )

        def change_hash_during_verify() -> None:
            with factory.begin() as session:
                session.execute(
                    update(User)
                    .where(User.id == issued.user.user_id)
                    .values(password_hash="replacement-private-hash")
                )

        service = build_service(
            store,
            clock=clock,
            hasher=FastHasher(verify_hook=change_hash_during_verify),
            token_factory=SequenceTokenFactory(TOKEN_B),
        )
        with pytest.raises(InvalidCredentials):
            asyncio.run(service.login(email=issued.user.email, password=VALID_PASSWORD))

        with factory() as session:
            sessions = session.scalar(select(func.count()).select_from(AuthSession))

    assert sessions == 1


def test_wrong_password_does_not_change_login_state(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A, TOKEN_B),
        )
        issued = asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        with factory() as session:
            original_login_at = session.get(User, issued.user.user_id).last_login_at

        clock.now += timedelta(hours=1)
        with pytest.raises(InvalidCredentials):
            asyncio.run(
                service.login(
                    email=issued.user.email,
                    password="incorrect password value",
                )
            )

        with factory() as session:
            user = session.get(User, issued.user.user_id)
            session_count = session.scalar(
                select(func.count()).select_from(AuthSession)
            )

    assert user is not None
    assert user.last_login_at == original_login_at
    assert session_count == 1


def test_login_retries_token_collision_without_leaking_partial_user_updates(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))
    hasher = FastHasher()

    with auth_store(tmp_path) as (store, factory):
        first_service = build_service(
            store,
            clock=clock,
            hasher=hasher,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        first = asyncio.run(
            first_service.register(email="first@example.com", password=VALID_PASSWORD)
        )
        second_service = build_service(
            store,
            clock=clock,
            hasher=hasher,
            token_factory=SequenceTokenFactory(TOKEN_B),
        )
        asyncio.run(
            second_service.register(email="second@example.com", password=VALID_PASSWORD)
        )

        clock.now += timedelta(hours=1)
        hasher.return_updated_hash = True
        login_service = build_service(
            store,
            clock=clock,
            hasher=hasher,
            token_factory=SequenceTokenFactory(TOKEN_B, TOKEN_C),
        )
        logged_in = asyncio.run(
            login_service.login(email=first.user.email, password=VALID_PASSWORD)
        )

        with factory() as session:
            user = session.get(User, first.user.user_id)
            sessions = session.execute(
                select(AuthSession).order_by(AuthSession.id)
            ).scalars().all()

    assert logged_in.token == TOKEN_C
    assert user is not None
    assert user.password_hash.startswith("updated$test$")
    assert user.last_login_at == clock.now.replace(tzinfo=None)
    assert [login_session.token_hash for login_session in sessions] == [
        sha256(TOKEN_A.encode("ascii")).digest(),
        sha256(TOKEN_B.encode("ascii")).digest(),
        sha256(TOKEN_C.encode("ascii")).digest(),
    ]


@pytest.mark.parametrize(
    ("stored_hash", "is_active"),
    [
        ("corrupted-private-hash", True),
        (None, False),
    ],
)
def test_corrupted_hash_and_inactive_account_share_fixed_login_error(
    tmp_path: Path,
    stored_hash: str | None,
    is_active: bool,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        with factory.begin() as session:
            values: dict[str, object] = {"is_active": is_active}
            if stored_hash is not None:
                values["password_hash"] = stored_hash
            session.execute(
                update(User).where(User.id == issued.user.user_id).values(**values)
            )

        with pytest.raises(InvalidCredentials) as caught:
            asyncio.run(
                service.login(email=issued.user.email, password=VALID_PASSWORD)
            )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "corrupted-private-hash" not in str(caught.value)
    assert issued.user.email not in str(caught.value)


def test_session_resolution_touch_expiry_inactivity_and_logout(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )

        clock.now += timedelta(minutes=4)
        assert asyncio.run(service.get_current_user(issued.token)) == issued.user
        with factory() as session:
            before_touch = session.execute(select(AuthSession)).scalar_one().last_seen_at

        clock.now += timedelta(minutes=2)
        assert asyncio.run(service.get_current_user(issued.token)) == issued.user
        with factory() as session:
            after_touch = session.execute(select(AuthSession)).scalar_one().last_seen_at
        assert before_touch == issued.expires_at.replace(tzinfo=None) - timedelta(days=7)
        assert after_touch == clock.now.replace(tzinfo=None)

        asyncio.run(service.logout(issued.token))
        asyncio.run(service.logout(issued.token))
        assert asyncio.run(service.get_current_user(issued.token)) is None
        asyncio.run(service.logout("malformed"))


def test_deactivating_account_invalidates_an_existing_session(tmp_path: Path) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        with factory.begin() as session:
            session.execute(
                update(User)
                .where(User.id == issued.user.user_id)
                .values(is_active=False)
            )

        assert asyncio.run(service.get_current_user(issued.token)) is None


def test_losing_concurrent_touch_keeps_the_newer_last_seen_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        token_hash = sha256(TOKEN_A.encode("ascii")).digest()
        stale_seen = clock.now.replace(tzinfo=None)
        newer_seen = stale_seen + timedelta(minutes=7)

        with factory.begin() as session:
            session.execute(update(AuthSession).values(last_seen_at=newer_seen))
        with factory() as session:
            stale_user = session.get(User, issued.user.user_id)
            current_session = session.execute(select(AuthSession)).scalar_one()
            stale_session = AuthSession(
                id=current_session.id,
                user_id=current_session.user_id,
                token_hash=current_session.token_hash,
                created_at=current_session.created_at,
                last_seen_at=stale_seen,
                expires_at=current_session.expires_at,
                revoked_at=None,
            )

        assert stale_user is not None
        original_select = auth_repository._select_active_session
        first_call = True

        def select_with_one_stale_read(
            session: Session,
            *,
            token_hash: bytes,
            checked_at: datetime,
            idle_since: datetime,
        ) -> tuple[User, AuthSession] | None:
            nonlocal first_call
            if first_call:
                first_call = False
                return stale_user, stale_session
            return original_select(
                session,
                token_hash=token_hash,
                checked_at=checked_at,
                idle_since=idle_since,
            )

        monkeypatch.setattr(
            auth_repository,
            "_select_active_session",
            select_with_one_stale_read,
        )
        older_request_time = clock.now.replace(tzinfo=None) + timedelta(minutes=6)
        with factory.begin() as session:
            user = auth_repository.resolve_active_session(
                session,
                token_hash=token_hash,
                checked_at=older_request_time,
                idle_since=older_request_time - timedelta(hours=24),
                touch_before=older_request_time - timedelta(minutes=5),
            )
        with factory() as session:
            stored_seen = session.execute(select(AuthSession)).scalar_one().last_seen_at

    assert user is not None
    assert user.id == issued.user.user_id
    assert stored_seen == newer_seen


def test_session_is_invalid_at_idle_boundary(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, _factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        clock.now += timedelta(hours=24)

        assert asyncio.run(service.get_current_user(issued.token)) is None


def test_session_is_invalid_at_absolute_expiry_with_recent_activity(
    tmp_path: Path,
) -> None:
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    with auth_store(tmp_path) as (store, factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=SequenceTokenFactory(TOKEN_A),
        )
        issued = asyncio.run(
            service.register(email="demo@example.com", password=VALID_PASSWORD)
        )
        with factory.begin() as session:
            session.execute(
                update(AuthSession).values(
                    last_seen_at=(issued.expires_at - timedelta(minutes=1)).replace(
                        tzinfo=None
                    )
                )
            )
        clock.now = issued.expires_at

        assert asyncio.run(service.get_current_user(issued.token)) is None


def test_token_factory_failure_has_no_private_exception_context(tmp_path: Path) -> None:
    private_detail = "PRIVATE TOKEN FACTORY DETAIL"
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))

    def failing_token_factory() -> str:
        raise RuntimeError(private_detail)

    with auth_store(tmp_path) as (store, _factory):
        service = build_service(
            store,
            clock=clock,
            token_factory=failing_token_factory,
        )
        with pytest.raises(AuthUnavailable) as caught:
            asyncio.run(
                service.register(email="demo@example.com", password=VALID_PASSWORD)
            )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_detail not in str(caught.value)
    assert private_detail not in repr(caught.value)


def test_database_errors_are_context_free_and_contain_no_provider_detail(
    tmp_path: Path,
) -> None:
    private_detail = "private SQL and password sentinel"
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'missing.sqlite3'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = SQLAlchemyAuthStore(factory)
    clock = MutableClock(datetime(2026, 8, 12, 4, 0, tzinfo=UTC))
    service = build_service(store, clock=clock)

    try:
        with pytest.raises(AuthUnavailable) as caught:
            asyncio.run(
                service.register(
                    email="private@example.com",
                    password=private_detail,
                )
            )
    finally:
        engine.dispose()

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert private_detail not in str(caught.value)
    assert "private@example.com" not in repr(caught.value)
