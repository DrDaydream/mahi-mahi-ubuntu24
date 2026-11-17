import copy
import os
from pathlib import Path
import subprocess

import pytest

from mahi_aws.settings import DEFAULTS


SCRIPT = Path(__file__).parents[1] / 'scripts/sync_source.sh'
BASH = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/bash')


def git(*args):
    return subprocess.run(['git', *map(str, args)], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    if not BASH.exists():
        pytest.skip('bash unavailable')
    origin = tmp_path / 'origin'
    git('init', '-b', 'consensus-rework', origin)
    (origin / 'source.txt').write_text('fixed revision\n')
    git('-C', origin, 'add', 'source.txt')
    git('-C', origin, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-m', 'fixture')
    return origin, git('-C', origin, 'rev-parse', 'HEAD')


def sync(target, upstream):
    origin, revision = upstream
    return subprocess.run([str(BASH), SCRIPT.as_posix(), target.as_posix(), revision,
                           origin.as_posix()], capture_output=True, text=True)


def test_existing_adapter_without_git_is_preserved_with_actionable_error(tmp_path, upstream):
    target = tmp_path / 'occupied'
    settings = target / 'aws/settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text('{"testbed_id":"mahi-ubuntu24"}\n')
    result = sync(target, upstream)
    assert result.returncode != 0
    assert 'remote.source_dir' in result.stderr
    assert 'git clone' not in result.stderr
    assert settings.read_text() == '{"testbed_id":"mahi-ubuntu24"}\n'
    assert not (target / '.git').exists()


def test_git_worktree_with_git_file_syncs_without_cloning(tmp_path, upstream):
    origin, revision = upstream
    target = tmp_path / 'worktree'
    git('-C', origin, 'remote', 'add', 'origin', origin.as_posix())
    git('-C', origin, 'worktree', 'add', '--detach', target, revision)
    assert (target / '.git').is_file()
    result = sync(target, upstream)
    assert result.returncode == 0, result.stderr
    assert git('-C', target, 'rev-parse', 'HEAD') == revision
    assert (target / 'source.txt').read_text() == 'fixed revision\n'


def test_default_source_directory_is_separate_from_deployment_checkout():
    settings = copy.deepcopy(DEFAULTS)
    assert settings['remote']['source_dir'] == '/home/ubuntu/experiments/mahi-mahi-source'
    assert settings['remote']['run_dir'] == '/home/ubuntu/experiments/mahi-mahi-aws-runs'
    assert settings['testbed_id'] == 'mahi-ubuntu24'
