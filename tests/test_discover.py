from pathlib import Path

from kitctl.discover import find_repos, nested_dirs


def make_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def test_finds_repos_to_depth_and_prunes(tmp_path: Path) -> None:
    code = tmp_path / "Code"
    a = make_repo(code / "a")
    b = make_repo(code / "org/b")
    make_repo(code / "org/deep/er/c")  # depth 4: beyond default 3
    make_repo(code / "node_modules/pkg")
    make_repo(code / ".hidden/repo")
    make_repo(a / "vendor-sub")  # nested inside a repo: skipped
    (code / "worktree").mkdir()
    (code / "worktree/.git").write_text("gitdir: elsewhere")  # .git file counts
    assert find_repos([code]) == sorted([a, b, code / "worktree"])
    assert code / "org/deep/er/c" in find_repos([code], max_depth=4)


def test_nested_dirs_skip_nested_repos_and_pruned(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    (repo / "packages/api").mkdir(parents=True)
    (repo / "node_modules/x").mkdir(parents=True)
    make_repo(repo / "sub")
    found = {p.relative_to(repo).as_posix() for p in nested_dirs(repo)}
    assert found == {"packages", "packages/api"}


def test_same_directory_listed_twice_is_found_once(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "Code/app")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path / "Code", target_is_directory=True)
    except OSError:
        alias = tmp_path / "Code"
    assert find_repos([tmp_path / "Code", alias]) == [repo]


def test_init_detects_parents_once_on_case_insensitive_fs(tmp_path: Path) -> None:
    from kitctl.init import _detect_parents

    (tmp_path / "Code").mkdir()
    (tmp_path / "src").mkdir()
    assert _detect_parents(tmp_path) == [str(tmp_path / "Code"), str(tmp_path / "src")]


def test_worktrees_skipped_by_default(tmp_path: Path) -> None:
    from kitctl.discover import find_repos_detail

    main_repo = make_repo(tmp_path / "Code/app")
    worktree = tmp_path / "Code/app-feature"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {main_repo}/.git/worktrees/app-feature\n")
    submodule_style = tmp_path / "Code/lib"
    submodule_style.mkdir()
    (submodule_style / ".git").write_text("gitdir: ../elsewhere/.git/modules/lib\n")
    repos, skipped = find_repos_detail([tmp_path / "Code"])
    assert repos == [main_repo, submodule_style] and skipped == [worktree]
    assert worktree in find_repos([tmp_path / "Code"], include_worktrees=True)
