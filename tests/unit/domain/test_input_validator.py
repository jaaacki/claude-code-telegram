"""
Tests for input validators.
"""

import pytest
from domain.validators.input_validator import (
    ValidatedCommand,
    ValidatedPath,
    ValidatedProxyUrl,
    ValidatedProjectName,
    ValidatedGitHubUrl,
    ValidatedText,
    ValidatedApiKey,
    validate_user_input,
)


class TestValidatedCommand:
    """Command Validation Tests"""

    def test_valid_command(self):
        """Valid command"""
        cmd = ValidatedCommand(command="ls -la")
        assert cmd.command == "ls -la"

    def test_command_too_long(self):
        """Command too long"""
        with pytest.raises(ValueError, match="too long"):
            ValidatedCommand(command="a" * 1001)

    def test_command_with_dangerous_chars(self):
        """Dangerous symbols"""
        with pytest.raises(ValueError, match="Dangerous character"):
            ValidatedCommand(command="ls && rm -rf")

        with pytest.raises(ValueError, match="Dangerous character"):
            ValidatedCommand(command="cat | grep test")

    def test_command_with_path_traversal(self):
        """Path traversal"""
        with pytest.raises(ValueError, match="Path traversal"):
            ValidatedCommand(command="cat ../../../etc/passwd")

    def test_command_strip_whitespace(self):
        """Removing spaces"""
        cmd = ValidatedCommand(command="  ls -la  ")
        assert cmd.command == "ls -la"


class TestValidatedPath:
    """Path Validation Tests"""

    def test_valid_path(self):
        """Valid path"""
        path = ValidatedPath(path="/root/projects")
        assert path.path == "/root/projects"

    def test_path_too_long(self):
        """Way too long"""
        with pytest.raises(ValueError, match="too long"):
            ValidatedPath(path="a" * 501)

    def test_path_with_path_traversal(self):
        """Path traversal"""
        with pytest.raises(ValueError, match="Path traversal"):
            ValidatedPath(path="/root/../etc/passwd")

    def test_path_with_null_bytes(self):
        """Null bytes"""
        with pytest.raises(ValueError, match="Null bytes"):
            ValidatedPath(path="/root\x00/projects")

    def test_path_with_windows_forbidden(self):
        """Prohibited characters Windows"""
        with pytest.raises(ValueError, match="Invalid character"):
            ValidatedPath(path="C:\\Projects\\Test<.txt")


class TestValidatedProxyUrl:
    """Validation tests proxy URLs"""

    def test_valid_http_proxy(self):
        """Valid HTTP proxy"""
        proxy = ValidatedProxyUrl(url="http://proxy.example.com:8080")
        assert proxy.url == "http://proxy.example.com:8080"

    def test_valid_socks5_proxy(self):
        """Valid SOCKS5 proxy"""
        proxy = ValidatedProxyUrl(url="socks5://proxy.example.com:1080")
        assert proxy.url == "socks5://proxy.example.com:1080"

    def test_proxy_with_auth(self):
        """Proxy with authentication"""
        proxy = ValidatedProxyUrl(url="http://user:pass@proxy.example.com:8080")
        assert proxy.url == "http://user:pass@proxy.example.com:8080"

    def test_proxy_invalid_scheme(self):
        """Invalid schema"""
        with pytest.raises(ValueError, match="Invalid proxy scheme"):
            ValidatedProxyUrl(url="ftp://proxy.example.com")

    def test_proxy_missing_hostname(self):
        """Absent hostname"""
        with pytest.raises(ValueError, match="Missing hostname"):
            ValidatedProxyUrl(url="http://")

    def test_proxy_invalid_port(self):
        """Invalid port"""
        with pytest.raises(ValueError, match="Invalid port"):
            ValidatedProxyUrl(url="http://proxy.example.com:99999")


class TestValidatedProjectName:
    """Project name validation tests"""

    def test_valid_project_name(self):
        """Valid name"""
        name = ValidatedProjectName(name="My Project")
        assert name.name == "My Project"

    def test_project_name_too_long(self):
        """Title too long"""
        with pytest.raises(ValueError, match="too long"):
            ValidatedProjectName(name="a" * 101)

    def test_project_name_with_invalid_chars(self):
        """Invalid characters"""
        with pytest.raises(ValueError, match="Invalid character"):
            ValidatedProjectName(name="Project/Name")

        with pytest.raises(ValueError, match="Invalid character"):
            ValidatedProjectName(name="Project:Name")

    def test_project_name_with_path_traversal(self):
        """Path traversal"""
        with pytest.raises(ValueError, match="Path traversal"):
            ValidatedProjectName(name="../etc")


class TestValidatedGitHubUrl:
    """Validation tests GitHub URLs"""

    def test_valid_github_url(self):
        """Valid GitHub URL"""
        url = ValidatedGitHubUrl(url="https://github.com/user/repo")
        assert url.url == "https://github.com/user/repo"

    def test_github_url_with_git(self):
        """GitHub URL With .git"""
        url = ValidatedGitHubUrl(url="https://github.com/user/repo.git")
        assert url.url == "https://github.com/user/repo.git"

    def test_github_url_invalid_scheme(self):
        """Invalid schema"""
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            ValidatedGitHubUrl(url="ftp://github.com/user/repo")

    def test_github_url_wrong_domain(self):
        """Invalid domain"""
        with pytest.raises(ValueError, match="must be from github.com"):
            ValidatedGitHubUrl(url="https://gitlab.com/user/repo")

    def test_github_url_missing_path(self):
        """Missing path"""
        with pytest.raises(ValueError, match="Invalid GitHub repository path"):
            ValidatedGitHubUrl(url="https://github.com/")


class TestValidatedText:
    """Text Validation Tests"""

    def test_valid_text(self):
        """Valid text"""
        text = ValidatedText(text="Hello, World!")
        assert text.text == "Hello, World!"

    def test_text_too_long(self):
        """Text is too long"""
        with pytest.raises(ValueError, match="too long"):
            ValidatedText(text="a" * 5001)

    def test_text_with_null_bytes(self):
        """Null bytes"""
        with pytest.raises(ValueError, match="Null bytes"):
            ValidatedText(text="Hello\x00World")

    def test_text_with_control_chars(self):
        """Control characters"""
        with pytest.raises(ValueError, match="Control characters"):
            ValidatedText(text="Hello\x01World")

    def test_text_with_newline_allowed(self):
        """Newline allowed"""
        text = ValidatedText(text="Line 1\nLine 2")
        assert "\n" in text.text


class TestValidatedApiKey:
    """Validation tests API keys"""

    def test_valid_api_key(self):
        """Valid API key"""
        key = ValidatedApiKey(key="sk-1234567890abcdef")
        assert key.key == "sk-1234567890abcdef"

    def test_api_key_too_long(self):
        """The key is too long"""
        with pytest.raises(ValueError, match="too long"):
            ValidatedApiKey(key="a" * 201)

    def test_api_key_with_whitespace(self):
        """Whitespace in the key"""
        with pytest.raises(ValueError, match="cannot contain whitespace"):
            ValidatedApiKey(key="sk-1234 5678")

    def test_api_key_strip(self):
        """Removing spaces"""
        key = ValidatedApiKey(key="  sk-1234567890  ")
        assert key.key == "sk-1234567890"


class TestValidateUserInput:
    """Function tests validate_user_input"""

    def test_valid_command(self):
        """Valid command"""
        success, error, value = validate_user_input('command', 'ls -la')
        assert success
        assert error == ""
        assert value == "ls -la"

    def test_invalid_command(self):
        """Invalid command"""
        success, error, value = validate_user_input('command', 'ls && rm -rf')
        assert not success
        assert "Dangerous character" in error
        assert value is None

    def test_unknown_type(self):
        """Unknown type"""
        success, error, value = validate_user_input('unknown', 'test')
        assert not success
        assert "Unknown input type" in error
