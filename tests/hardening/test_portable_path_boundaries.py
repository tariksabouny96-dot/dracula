"""Cross-platform workspace path security regressions."""
import os
import pytest

from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError


def test_windows_absolute_path_not_accepted_as_posix_relative(tmp_path):
    gateway = ToolGateway(workspace_root=tmp_path)
    if os.name != 'nt':
        for candidate in (r'C:\Windows\System32', r'\\server\share\secrets.txt',
                          r'..\..\outside.txt', r'\absolute\file'):
            with pytest.raises(PermissionDeniedError):
                gateway.validate_path(candidate)


def test_symlink_escape_and_invalid_paths_blocked(tmp_path):
    gateway = ToolGateway(workspace_root=tmp_path)
    for candidate in ('', '\x00secret', '../private'):
        with pytest.raises(PermissionDeniedError):
            gateway.validate_path(candidate)
    outside = tmp_path.parent / 'private'
    outside.mkdir(exist_ok=True)
    link = tmp_path / 'escape'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks not supported')
    with pytest.raises(PermissionDeniedError):
        gateway.validate_path('escape/file.txt')


def test_workspace_relative_path_allowed(tmp_path):
    gw = ToolGateway(workspace_root=tmp_path)
    assert gw.validate_path('safe/file.txt') == tmp_path / 'safe' / 'file.txt'
