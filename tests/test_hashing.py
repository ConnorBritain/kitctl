import unicodedata
from pathlib import Path

from kitctl.hashing import HASH_SCHEME, HashCache, file_digest, is_junk, tree_digest


def test_scheme_is_versioned() -> None:
    assert HASH_SCHEME == 2


def test_crlf_cr_and_lf_text_hash_equal(tmp_path: Path) -> None:
    variants = [b"a\nb\nc\n", b"a\r\nb\r\nc\r\n", b"a\rb\rc\r"]
    digests = set()
    for index, data in enumerate(variants):
        path = tmp_path / f"f{index}"
        path.write_bytes(data)
        digests.add(file_digest(path))
    assert len(digests) == 1


def test_crlf_split_across_read_boundary(tmp_path: Path) -> None:
    body = b"x" * (8192 - 1) + b"\r\n" + b"y" * 70000 + b"\r\n"
    crlf, lf = tmp_path / "crlf", tmp_path / "lf"
    crlf.write_bytes(body)
    lf.write_bytes(body.replace(b"\r\n", b"\n"))
    assert file_digest(crlf) == file_digest(lf)


def test_binary_files_are_not_normalized(tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(b"\0\r\n")
    two.write_bytes(b"\0\n")
    assert file_digest(one) != file_digest(two)


def test_junk_names() -> None:
    assert is_junk(".DS_Store", False) and is_junk("Thumbs.db", False)
    assert is_junk("desktop.ini", False) and is_junk("mod.pyc", False)
    assert is_junk("__pycache__", True) and is_junk(".git", True)
    assert not is_junk("SKILL.md", False) and not is_junk("scripts", True)


def test_tree_digest_nfc_and_order_independent() -> None:
    nfd = unicodedata.normalize("NFD", "café/SKILL.md")
    nfc = unicodedata.normalize("NFC", "café/SKILL.md")
    assert nfd != nfc
    assert tree_digest([(nfd, "aa"), ("b", "bb")]) == tree_digest([("b", "bb"), (nfc, "aa")])


def test_hash_cache_reuses_and_invalidates(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("one")
    store = tmp_path / "cache/hashes.json"
    cache = HashCache(store)
    first = cache.digest(target)
    cache.save()
    assert store.is_file()
    reloaded = HashCache(store)
    assert reloaded.digest(target) == first
    target.write_text("two!")
    assert reloaded.digest(target) != first
