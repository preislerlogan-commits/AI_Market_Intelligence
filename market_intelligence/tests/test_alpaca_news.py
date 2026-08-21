"""Tests for market_intelligence.data_connectors.alpaca_news.

These tests must never make live HTTP requests or read real credentials.
All HTTP interaction is stubbed via httpx.MockTransport, and settings are
built from monkeypatched environment variables pointed at a nonexistent
.env file, mirroring market_intelligence/tests/test_alpaca_market_data.py.
"""

from pathlib import Path

import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_news import (
    NEWS_BASE_URL,
    AlpacaNewsClient,
    AlpacaNewsCredentialsMissingError,
    AlpacaNewsError,
    AlpacaNewsInvalidInputError,
    normalize_limit,
    normalize_sort,
    normalize_symbols,
    normalize_timestamp,
)

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]

FAKE_ALPACA_KEY = "unit-test-alpaca-key"
FAKE_ALPACA_SECRET = "unit-test-alpaca-secret"


@pytest.fixture(autouse=True)
def clear_credential_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def configured_settings(monkeypatch, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("ALPACA_API_KEY", FAKE_ALPACA_KEY)
    monkeypatch.setenv("ALPACA_API_SECRET", FAKE_ALPACA_SECRET)
    return Settings(_env_file=isolated_env_file)


def unconfigured_settings(isolated_env_file: Path) -> Settings:
    return Settings(_env_file=isolated_env_file)


def mock_client(handler) -> httpx.Client:
    return httpx.Client(base_url=NEWS_BASE_URL, transport=httpx.MockTransport(handler))


def sample_article(
    article_id: int | str = 12345,
    headline: str = "Fed signals rate pause",
    source: str = "benzinga",
    url: str = "https://example.com/news/12345",
    summary: str | None = "The Fed indicated a pause in rate hikes.",
    created_at: str | None = "2026-08-20T12:00:00Z",
    updated_at: str | None = "2026-08-20T12:05:00Z",
    symbols: list[str] | None = None,
) -> dict:
    return {
        "id": article_id,
        "headline": headline,
        "author": "Jane Doe",
        "created_at": created_at,
        "updated_at": updated_at,
        "summary": summary,
        "content": "<p>full article content</p>",
        "images": [],
        "url": url,
        "symbols": symbols if symbols is not None else ["SPY"],
        "source": source,
    }


def news_payload(articles: list) -> dict:
    return {"news": articles, "next_page_token": None}


# --- credentials -----------------------------------------------------------


def test_is_configured_false_without_credentials(isolated_env_file):
    client = AlpacaNewsClient(settings=unconfigured_settings(isolated_env_file))
    assert client.is_configured() is False


def test_is_configured_true_with_credentials(monkeypatch, isolated_env_file):
    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    assert client.is_configured() is True


def test_get_news_raises_without_credentials(isolated_env_file):
    client = AlpacaNewsClient(settings=unconfigured_settings(isolated_env_file))
    with pytest.raises(AlpacaNewsCredentialsMissingError):
        client.get_news("SPY")


def test_get_news_without_credentials_makes_zero_requests(isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=unconfigured_settings(isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsCredentialsMissingError):
        client.get_news("SPY", client=http_client)

    assert calls == []


# --- valid request and normalization ----------------------------------------


def test_get_news_returns_normalized_items_on_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1beta1/news"
        assert request.headers["APCA-API-KEY-ID"] == FAKE_ALPACA_KEY
        assert request.headers["APCA-API-SECRET-KEY"] == FAKE_ALPACA_SECRET
        assert request.url.params["symbols"] == "SPY"
        assert request.url.params["limit"] == "10"
        assert request.url.params["sort"] == "desc"
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    assert len(items) == 1
    item = items[0]
    assert item.provider == "alpaca"
    assert item.provider_article_id == "12345"
    assert item.headline == "Fed signals rate pause"
    assert item.source == "benzinga"
    assert item.url == "https://example.com/news/12345"
    assert item.summary == "The Fed indicated a pause in rate hikes."
    assert item.created_at == "2026-08-20T12:00:00Z"
    assert item.updated_at == "2026-08-20T12:05:00Z"
    assert item.related_symbols == ("SPY",)
    assert item.retrieved_at is not None


def test_get_news_normalizes_lowercase_symbols_and_builds_comma_list(
    monkeypatch, isolated_env_file
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbols"] == "SPY,QQQ"
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.get_news("  spy , qqq ", client=http_client)


def test_get_news_passes_start_end_sort_limit(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "5"
        assert request.url.params["sort"] == "asc"
        assert request.url.params["start"] == "2026-08-01T00:00:00Z"
        assert request.url.params["end"] == "2026-08-20T00:00:00Z"
        return httpx.Response(200, json=news_payload([]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.get_news(
            "SPY",
            limit=5,
            sort="asc",
            start="2026-08-01T00:00:00Z",
            end="2026-08-20T00:00:00Z",
            client=http_client,
        )


# --- invalid inputs make zero requests --------------------------------------

INVALID_SYMBOLS = [
    "",
    "   ",
    "../../etc/passwd",
    "AAPL/AAPL",
    "http://evil.com",
    "AA PL",
    "AA\nPL",
    "AA\x00PL",
    [],
    123,
    None,
]

INVALID_LIMITS = [0, -1, 51, 1000, "10", True, False, None, 3.5]

INVALID_SORTS = ["ASCENDING", "", "   ", "up", None, 123]

INVALID_TIMESTAMPS = [
    "",
    "not-a-date",
    "2026-13-40T00:00:00Z",
    "2026-08-20T00:00:00Z; DROP TABLE news;",
    "A" * 41,
    123,
    None,
    True,
    False,
    "2026-08-20",  # date-only, no time component
    "2026-08-20T12:00:00",  # naive, no UTC offset
    "2026-08-20T12:00:00+0000",  # malformed offset, missing colon
    "2026-08-20T12:00:00+25:00",  # out-of-range offset hour
    "2026-08-20T12:00:00-04",  # incomplete offset
    "2026-02-30T00:00:00Z",  # invalid calendar date (no Feb 30)
    "2026-08-20 12:00:00Z",  # space instead of "T" separator
]

# ``start``/``end`` of None means "not provided" at the get_news layer (it is
# omitted from the request entirely), so it must be excluded here — only
# normalize_timestamp() itself treats None as an invalid value when called
# directly.
INVALID_TIMESTAMPS_FOR_CLIENT = [value for value in INVALID_TIMESTAMPS if value is not None]


@pytest.mark.parametrize("raw", INVALID_SYMBOLS)
def test_get_news_invalid_symbol_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsInvalidInputError):
        client.get_news(raw, client=http_client)

    assert calls == []


@pytest.mark.parametrize("raw", INVALID_LIMITS)
def test_get_news_invalid_limit_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsInvalidInputError):
        client.get_news("SPY", limit=raw, client=http_client)

    assert calls == []


@pytest.mark.parametrize("raw", INVALID_SORTS)
def test_get_news_invalid_sort_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsInvalidInputError):
        client.get_news("SPY", sort=raw, client=http_client)

    assert calls == []


@pytest.mark.parametrize("raw", INVALID_TIMESTAMPS_FOR_CLIENT)
def test_get_news_invalid_start_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsInvalidInputError):
        client.get_news("SPY", start=raw, client=http_client)

    assert calls == []


@pytest.mark.parametrize("raw", INVALID_TIMESTAMPS_FOR_CLIENT)
def test_get_news_invalid_end_makes_zero_requests(monkeypatch, isolated_env_file, raw):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsInvalidInputError):
        client.get_news("SPY", end=raw, client=http_client)

    assert calls == []


def test_get_news_too_many_symbols_makes_zero_requests(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    too_many = ",".join(f"SYM{i}" for i in range(21))
    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsInvalidInputError):
        client.get_news(too_many, client=http_client)

    assert calls == []


# --- normalize_* validators --------------------------------------------------


def test_normalize_symbols_accepts_comma_separated_string():
    assert normalize_symbols("spy, qqq") == ("SPY", "QQQ")


def test_normalize_symbols_dedupes_preserving_order():
    assert normalize_symbols("SPY,spy,QQQ") == ("SPY", "QQQ")


def test_normalize_symbols_accepts_iterable():
    assert normalize_symbols(["spy", "qqq"]) == ("SPY", "QQQ")


@pytest.mark.parametrize("raw", INVALID_SYMBOLS)
def test_normalize_symbols_rejects_invalid_input(raw):
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_symbols(raw)


@pytest.mark.parametrize("raw", INVALID_LIMITS)
def test_normalize_limit_rejects_invalid_input(raw):
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_limit(raw)


def test_normalize_limit_accepts_boundaries():
    assert normalize_limit(1) == 1
    assert normalize_limit(50) == 50


@pytest.mark.parametrize("raw", INVALID_SORTS)
def test_normalize_sort_rejects_invalid_input(raw):
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_sort(raw)


def test_normalize_sort_accepts_and_lowercases():
    assert normalize_sort("ASC") == "asc"
    assert normalize_sort(" Desc ") == "desc"


@pytest.mark.parametrize("raw", INVALID_TIMESTAMPS)
def test_normalize_timestamp_rejects_invalid_input(raw):
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_timestamp(raw, field_name="start")


def test_normalize_timestamp_accepts_valid_rfc3339():
    assert normalize_timestamp("2026-08-20T12:00:00Z", field_name="start") == "2026-08-20T12:00:00Z"


def test_normalize_timestamp_accepts_numeric_offset_and_normalizes_to_utc():
    # -04:00 offset should be converted to the equivalent UTC "Z" timestamp.
    assert (
        normalize_timestamp("2026-08-20T08:00:00-04:00", field_name="start")
        == "2026-08-20T12:00:00Z"
    )


def test_normalize_timestamp_positive_offset_normalizes_to_utc():
    assert (
        normalize_timestamp("2026-08-20T14:30:00+02:30", field_name="start")
        == "2026-08-20T12:00:00Z"
    )


def test_normalize_timestamp_z_offset_normalized_representation_is_utc_z():
    normalized = normalize_timestamp("2026-08-20T12:00:00Z", field_name="start")
    assert normalized.endswith("Z")
    assert normalized == "2026-08-20T12:00:00Z"


def test_normalize_timestamp_rejects_naive_datetime():
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_timestamp("2026-08-20T12:00:00", field_name="start")


def test_normalize_timestamp_rejects_date_only():
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_timestamp("2026-08-20", field_name="start")


@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-20T12:00:00+0000",  # missing colon in offset
        "2026-08-20T12:00:00+25:00",  # out-of-range offset hour
        "2026-08-20T12:00:00-04",  # incomplete offset
        "2026-08-20T12:00:00+00:60",  # out-of-range offset minute
    ],
)
def test_normalize_timestamp_rejects_malformed_offset(raw):
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_timestamp(raw, field_name="start")


def test_normalize_timestamp_rejects_invalid_calendar_date():
    with pytest.raises(AlpacaNewsInvalidInputError):
        normalize_timestamp("2026-02-30T00:00:00Z", field_name="start")


def test_normalize_timestamp_error_never_echoes_untrusted_input():
    secret_marker = "SUPER-SECRET-INPUT-VALUE"
    with pytest.raises(AlpacaNewsInvalidInputError) as exc_info:
        normalize_timestamp(f"not-a-date-{secret_marker}", field_name="start")

    assert secret_marker not in str(exc_info.value)


# --- start/end ordering -------------------------------------------------------


def test_get_news_rejects_start_after_end_with_zero_requests(monkeypatch, isolated_env_file):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsInvalidInputError):
        client.get_news(
            "SPY",
            start="2026-08-20T00:00:00Z",
            end="2026-08-01T00:00:00Z",
            client=http_client,
        )

    assert calls == []


def test_get_news_accepts_equal_start_and_end(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["start"] == "2026-08-20T00:00:00Z"
        assert request.url.params["end"] == "2026-08-20T00:00:00Z"
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news(
            "SPY",
            start="2026-08-20T00:00:00Z",
            end="2026-08-20T00:00:00Z",
            client=http_client,
        )

    assert len(items) == 1


# --- HTTP/network errors -----------------------------------------------------


def test_get_news_sanitized_error_on_http_status_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError) as exc_info:
        client.get_news("SPY", client=http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message
    assert "403" in message


def test_get_news_sanitized_error_on_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError) as exc_info:
        client.get_news("SPY", client=http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message


# --- malformed/non-object responses -----------------------------------------


def test_get_news_sanitized_error_on_malformed_json(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not valid json")

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError) as exc_info:
        client.get_news("SPY", client=http_client)

    assert "{not valid json" not in str(exc_info.value)


def test_get_news_sanitized_error_on_non_object_json(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError):
        client.get_news("SPY", client=http_client)


def test_get_news_sanitized_error_on_missing_news_key(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"next_page_token": None})

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError):
        client.get_news("SPY", client=http_client)


def test_get_news_sanitized_error_on_non_list_news_field(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"news": "not-a-list"})

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError):
        client.get_news("SPY", client=http_client)


# --- missing/malformed article fields ----------------------------------------


def test_get_news_skips_articles_missing_required_fields(monkeypatch, isolated_env_file):
    articles = [
        {"id": 1},  # missing headline/source/url
        {"id": 2, "headline": "", "source": "benzinga", "url": "https://x"},  # blank headline
        {"id": 3, "headline": "Valid", "source": "benzinga", "url": "https://x"},
        "not-a-dict",
        sample_article(article_id=4),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    ids = {item.provider_article_id for item in items}
    assert ids == {"3", "4"}


def test_get_news_missing_id_article_skipped(monkeypatch, isolated_env_file):
    articles = [
        {"headline": "No id", "source": "benzinga", "url": "https://x"},
        sample_article(article_id=99),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    assert [item.provider_article_id for item in items] == ["99"]


def test_get_news_missing_optional_fields_default_to_none(monkeypatch, isolated_env_file):
    articles = [
        sample_article(article_id=1, summary=None, created_at=None, updated_at=None, symbols=[])
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    item = items[0]
    assert item.summary is None
    assert item.created_at is None
    assert item.updated_at is None
    assert item.related_symbols == ()


# --- systemic article-schema failure vs. individual skips ---------------------


def test_get_news_empty_provider_list_succeeds_with_zero_articles(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload([]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    assert items == []


def test_get_news_raises_when_all_articles_are_malformed(monkeypatch, isolated_env_file):
    articles = [
        {"id": 1},  # missing headline/source/url
        {"id": 2, "headline": "", "source": "benzinga", "url": "https://x"},  # blank headline
        "not-a-dict",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError):
        client.get_news("SPY", client=http_client)


def test_get_news_all_malformed_error_never_leaks_article_contents(monkeypatch, isolated_env_file):
    secret_headline = "SECRET-HEADLINE-CONTENT"
    secret_url = "https://internal.example.com/SECRET-URL-PATH"
    articles = [
        {"id": 1, "headline": secret_headline, "url": secret_url},  # missing source
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError) as exc_info:
        client.get_news("SPY", client=http_client)

    message = str(exc_info.value)
    assert secret_headline not in message
    assert secret_url not in message
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message


def test_get_news_mixed_valid_and_malformed_succeeds_with_valid_only(
    monkeypatch, isolated_env_file
):
    articles = [
        {"id": 1},  # malformed: missing headline/source/url
        sample_article(article_id=2, headline="Valid article"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    assert [item.provider_article_id for item in items] == ["2"]


def test_check_connection_all_malformed_returns_invalid_response(monkeypatch, isolated_env_file):
    articles = [
        {"id": 1},  # missing headline/source/url
        "not-a-dict",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.status_category == "invalid_response"
    assert status.article_count == 0
    assert status.newest_publication_timestamp is None


def test_check_connection_all_malformed_repr_never_leaks_contents(monkeypatch, isolated_env_file):
    secret_headline = "SECRET-HEADLINE-CONTENT"
    articles = [{"id": 1, "headline": secret_headline}]  # missing source/url

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    status_repr = repr(status)
    assert secret_headline not in status_repr
    assert FAKE_ALPACA_KEY not in status_repr
    assert FAKE_ALPACA_SECRET not in status_repr


def test_check_connection_mixed_valid_and_malformed_succeeds_with_valid_only(
    monkeypatch, isolated_env_file
):
    articles = [
        {"id": 1},  # malformed
        sample_article(article_id=2),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is True
    assert status.status_category == "2xx"
    assert status.article_count == 1


# --- timestamps remain distinct ----------------------------------------------


def test_timestamps_remain_distinct(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    item = items[0]
    assert item.created_at == "2026-08-20T12:00:00Z"
    assert item.updated_at == "2026-08-20T12:05:00Z"
    assert item.created_at != item.updated_at
    assert item.retrieved_at != item.created_at
    assert item.retrieved_at != item.updated_at
    assert item.retrieved_at is not None and len(item.retrieved_at) > 0


# --- deduplication by provider article ID -------------------------------------


def test_get_news_deduplicates_by_provider_article_id(monkeypatch, isolated_env_file):
    articles = [
        sample_article(article_id=42, headline="First version"),
        sample_article(article_id=42, headline="Duplicate should be dropped"),
        sample_article(article_id=43, headline="Different article"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload(articles))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    assert len(items) == 2
    ids = [item.provider_article_id for item in items]
    assert ids == ["42", "43"]
    assert items[0].headline == "First version"


# --- secrets never appear in exceptions or status representations ------------


def test_secrets_never_in_get_news_exception_messages(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client, pytest.raises(AlpacaNewsError) as exc_info:
        client.get_news("SPY", client=http_client)

    message = str(exc_info.value)
    assert FAKE_ALPACA_KEY not in message
    assert FAKE_ALPACA_SECRET not in message


def test_secrets_never_in_check_connection_status_repr(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert FAKE_ALPACA_KEY not in repr(status)
    assert FAKE_ALPACA_SECRET not in repr(status)


def test_secrets_sent_as_headers_not_query_params(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert FAKE_ALPACA_KEY not in str(request.url)
        assert FAKE_ALPACA_SECRET not in str(request.url)
        assert request.headers["APCA-API-KEY-ID"] == FAKE_ALPACA_KEY
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        client.get_news("SPY", client=http_client)


def test_secrets_never_in_normalized_item_repr(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload([sample_article()]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        items = client.get_news("SPY", client=http_client)

    assert FAKE_ALPACA_KEY not in repr(items[0])
    assert FAKE_ALPACA_SECRET not in repr(items[0])


# --- check_connection ---------------------------------------------------------


def test_check_connection_not_configured(isolated_env_file):
    client = AlpacaNewsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("SPY")

    assert status.configured is False
    assert status.success is False
    assert status.status_category == "not_configured"
    assert status.symbol == "SPY"
    assert status.article_count == 0
    assert status.newest_publication_timestamp is None


def test_check_connection_success(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=news_payload(
                [
                    sample_article(article_id=1, created_at="2026-08-19T10:00:00Z"),
                    sample_article(article_id=2, created_at="2026-08-20T10:00:00Z"),
                ]
            ),
        )

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.configured is True
    assert status.success is True
    assert status.status_category == "2xx"
    assert status.symbol == "SPY"
    assert status.article_count == 2
    assert status.newest_publication_timestamp == "2026-08-20T10:00:00Z"


def test_check_connection_invalid_symbol(isolated_env_file):
    client = AlpacaNewsClient(settings=unconfigured_settings(isolated_env_file))
    status = client.check_connection("")

    assert status.success is False
    assert status.status_category == "invalid_input"
    assert status.symbol == ""


def test_check_connection_http_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.configured is True
    assert status.success is False
    assert status.status_category == "4xx"
    assert status.article_count == 0


def test_check_connection_network_error(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.status_category == "network_error"


def test_check_connection_invalid_response(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is False
    assert status.status_category == "invalid_response"


def test_check_connection_no_articles(monkeypatch, isolated_env_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=news_payload([]))

    client = AlpacaNewsClient(settings=configured_settings(monkeypatch, isolated_env_file))
    with mock_client(handler) as http_client:
        status = client.check_connection("SPY", client=http_client)

    assert status.success is True
    assert status.article_count == 0
    assert status.newest_publication_timestamp is None
