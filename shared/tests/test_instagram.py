import pytest

from shared.instagram import PageRefError, parse_page_ref, parse_post_ref


@pytest.mark.parametrize("value, expected", [
    ("foo", "foo"), ("@foo", "foo"), ("FOO", "foo"),
    (" Foo.Bar_9 ", "foo.bar_9"), ("a", "a"), ("a1", "a1"),
    ("1a", "1a"), ("_foo", "_foo"), ("foo_", "foo_"),
    ("foo.bar", "foo.bar"), ("foo_bar", "foo_bar"),
    ("a" * 30, "a" * 30),
    ("https://instagram.com/foo", "foo"),
    ("http://instagram.com/foo", "foo"),
    ("https://www.instagram.com/foo/", "foo"),
    ("https://m.instagram.com/foo?x=1#bio", "foo"),
    ("https://instagr.am/foo", "foo"),
    ("https://www.instagram.com/foo/?igsh=abc", "foo"),
    ("https://instagram.com/FOO", "foo"),
    ("javascript:alert(1)", "invalid_scheme"),
    ("data:text/html,<b>x</b>", "invalid_scheme"),
    ("file:///foo", "invalid_scheme"), ("ftp://instagram.com/foo", "invalid_scheme"),
    ("https://evil.com/foo", "invalid_host"),
    ("https://instagram.com.evil.com/foo", "invalid_host"),
    ("https://user:pass@instagram.com/foo", "userinfo"),
    ("https://instagram.com:8080/foo", "port"),
    ("https://instagram.com/foo/../admin", "invalid_path"),
    ("https://instagram.com/foo%2Fbar", "encoded_path_separator"),
    ("https://instagram.com/foo%2Ebar", "encoded_path_separator"),
    ("https://instagram.com/p/ABC/", "reserved_path"),
    ("https://instagram.com/reels/ABC", "reserved_path"),
    ("instagram.com/foo", "invalid_handle"),
    ("foo\nbar", "control_character"), ("foo\x00", "control_character"),
    ("foo bar", "internal_whitespace"), ("", "empty"), ("   ", "empty"),
    ("a" * 500, "too_long"), ("ｆｏｏ", "non_ascii"),
    ("مرحبا", "non_ascii"), ("foo\u202ebar", "non_ascii"),
    ("<script>alert(1)</script>", "invalid_handle"),
    (".foo", "invalid_handle"), ("foo.", "invalid_handle"),
    ("foo..bar", "invalid_handle"), ("https://instagram.com//foo", "invalid_path"),
    ("https://instagram.com/foo/bar", "invalid_path"),
    ("https://instagram.com/p", "reserved_path"),
    ("https://instagram.com/accounts/login", "reserved_path"),
    (r"\instagram.com\foo", "backslash"),
    ("https://instagram.com/%2e%2e/admin", "invalid_path"),
    ("https://instagram.com@evil.com/foo", "userinfo"),
])
def test_parse_page_ref_allowlist(value, expected):
    if expected in {
        "foo", "a", "a1", "1a", "_foo", "foo_", "foo.bar",
        "foo_bar", "foo.bar_9", "_foo", "a" * 30,
    }:
        assert parse_page_ref(value) == expected
    else:
        with pytest.raises(PageRefError) as error:
            parse_page_ref(value)
        assert error.value.reason == expected


@pytest.mark.parametrize("value, expected", [
    ("https://instagram.com/p/AbCde_12/", "AbCde_12"),
    ("http://www.instagram.com/reel/AbCde-12?igsh=x#caption", "AbCde-12"),
    ("https://m.instagram.com/tv/AbCde12", "AbCde12"),
    ("https://instagr.am/p/AbCde12/?x=1", "AbCde12"),
])
def test_parse_post_ref_valid_urls(value, expected):
    assert parse_post_ref(value) == expected


@pytest.mark.parametrize("value, reason", [
    ("https://instagram.com/shop", "invalid_path"),
    ("https://instagram.com/p/abc", "invalid_shortcode"),
    ("https://instagram.com/p/abcde/extra", "invalid_path"),
    ("https://evil.test/p/AbCde12", "invalid_host"),
    ("ftp://instagram.com/p/AbCde12", "invalid_scheme"),
    ("https://u:p@instagram.com/p/AbCde12", "userinfo"),
    ("https://instagram.com:443/p/AbCde12", "port"),
    ("https://instagram.com/p/AbCde%2F12", "invalid_shortcode"),
    ("https://instagram.com/p/AbCde12\n", "control_character"),
    ("https://instagram.com/p/AbCde 12", "internal_whitespace"),
    ("javascript:alert(1)", "invalid_scheme"),
])
def test_parse_post_ref_rejects_invalid_and_hostile_inputs(value, reason):
    with pytest.raises(PageRefError) as error:
        parse_post_ref(value)
    assert error.value.reason == reason
