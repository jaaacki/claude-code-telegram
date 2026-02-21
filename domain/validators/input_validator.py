"""
Input validation utilities for safety.

Provides validation for all user inputs.
"""

import re
import logging
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, validator, Field

logger = logging.getLogger(__name__)


# === Common Validators ===

class ValidatedCommand(BaseModel):
    """Validated command from user"""
    command: str = Field(..., min_length=1, max_length=1000)

    @validator('command')
    def validate_command(cls, v):
        """Command validation for dangerous characters"""
        # Length
        if len(v) > 1000:
            raise ValueError('Command too long (max 1000 characters)')

        # Dangerous symbols (basic check)
        dangerous_patterns = ['&&', '||', ';', '`', '$(', '<(', '>', '|']
        for pattern in dangerous_patterns:
            if pattern in v:
                raise ValueError(f'Dangerous character sequence detected: {pattern}')

        # Path traversal
        if '../' in v or '..\\' in v:
            raise ValueError('Path traversal detected')

        # Null bytes
        if '\x00' in v:
            raise ValueError('Null bytes detected')

        return v.strip()


class ValidatedPath(BaseModel):
    """Validated path"""
    path: str = Field(..., min_length=1, max_length=500)

    @validator('path')
    def validate_path(cls, v):
        """Path Validation"""
        # Length
        if len(v) > 500:
            raise ValueError('Path too long (max 500 characters)')

        # Path traversal
        if '../' in v or '..\\' in v:
            raise ValueError('Path traversal detected')

        # Null bytes
        if '\x00' in v:
            raise ValueError('Null bytes detected')

        # Checking for valid characters
        # Windows:不允许 <>:"|?*
        # Unix: only null And /
        invalid_windows = ['<', '>', ':', '"', '|', '?', '*']
        if any(char in v for char in invalid_windows):
            raise ValueError(f'Invalid character in path (Windows forbidden)')

        return v.strip()


class ValidatedProxyUrl(BaseModel):
    """Validated proxy URL"""
    url: str = Field(..., min_length=1, max_length=200)

    @validator('url')
    def validate_proxy_url(cls, v):
        """Validation proxy URL"""
        from urllib.parse import urlparse

        try:
            result = urlparse(v)

            # Scheme examination
            if result.scheme not in ['http', 'https', 'socks5', 'socks5h']:
                raise ValueError(
                    "Invalid proxy scheme. Must be http, https, socks5, or socks5h"
                )

            # Hostname examination
            if not result.hostname:
                raise ValueError("Missing hostname in proxy URL")

            # Port check (if specified)
            if result.port is not None and not (1 <= result.port <= 65535):
                raise ValueError(f"Invalid port: {result.port}")

            # Length
            if len(v) > 200:
                raise ValueError('Proxy URL too long (max 200 characters)')

            return v.strip()

        except Exception as e:
            raise ValueError(f'Invalid proxy URL: {e}')


class ValidatedProjectName(BaseModel):
    """Validated project name"""
    name: str = Field(..., min_length=1, max_length=100)

    @validator('name')
    def validate_project_name(cls, v):
        """Project name validation"""
        # Length
        if len(v) > 100:
            raise ValueError('Project name too long (max 100 characters)')

        # Dangerous symbols
        if any(char in v for char in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']):
            raise ValueError('Invalid character in project name')

        # Path traversal
        if '../' in v or '..\\' in v:
            raise ValueError('Path traversal detected')

        # Null bytes
        if '\x00' in v:
            raise ValueError('Null bytes detected')

        return v.strip()


class ValidatedGitHubUrl(BaseModel):
    """Validated GitHub URL"""
    url: str = Field(..., min_length=1, max_length=200)

    @validator('url')
    def validate_github_url(cls, v):
        """Validation GitHub URL"""
        from urllib.parse import urlparse

        try:
            result = urlparse(v)

            # Scheme examination
            if result.scheme not in ['http', 'https']:
                raise ValueError("Invalid URL scheme. Must be http or https")

            # Domain examination
            if 'github.com' not in result.netloc.lower():
                raise ValueError("URL must be from github.com")

            # Path examination
            if not result.path or result.path == '/':
                raise ValueError("Invalid GitHub repository path")

            # Length
            if len(v) > 200:
                raise ValueError('GitHub URL too long (max 200 characters)')

            return v.strip()

        except Exception as e:
            raise ValueError(f'Invalid GitHub URL: {e}')


class ValidatedText(BaseModel):
    """Validated text (general case)"""
    text: str = Field(..., min_length=1, max_length=5000)

    @validator('text')
    def validate_text(cls, v):
        """Basic text validation"""
        # Length
        if len(v) > 5000:
            raise ValueError('Text too long (max 5000 characters)')

        # Null bytes
        if '\x00' in v:
            raise ValueError('Null bytes detected')

        # Control characters (except newline, tab, carriage return)
        control_chars = set(range(0, 32)) - {9, 10, 13}  # \t, \n, \r
        if any(ord(c) in control_chars for c in v):
            raise ValueError('Control characters detected')

        return v


class ValidatedApiKey(BaseModel):
    """Validated API key"""
    key: str = Field(..., min_length=1, max_length=200)

    @validator('key')
    def validate_api_key(cls, v):
        """Validation API key"""
        # Length
        if len(v) > 200:
            raise ValueError('API key too long (max 200 characters)')

        # Check for whitespace
        if any(c.isspace() for c in v):
            raise ValueError('API key cannot contain whitespace')

        # Null bytes
        if '\x00' in v:
            raise ValueError('Null bytes detected')

        return v.strip()


# === Validator Functions ===

def validate_user_input(input_type: str, value: str) -> tuple[bool, str, any]:
    """
    User input validation.

    Args:
        input_type: Input type (command, path, proxy_url, project_name, github_url, text, api_key)
        value: Validation value

    Returns:
        (success, error_message, validated_value)
    """
    validators = {
        'command': ValidatedCommand,
        'path': ValidatedPath,
        'proxy_url': ValidatedProxyUrl,
        'project_name': ValidatedProjectName,
        'github_url': ValidatedGitHubUrl,
        'text': ValidatedText,
        'api_key': ValidatedApiKey,
    }

    validator_class = validators.get(input_type)
    if not validator_class:
        return False, f"Unknown input type: {input_type}", None

    try:
        validated = validator_class(**{input_type if input_type != 'proxy_url' else 'url': value})
        return True, "", validated.__getattribute__(
            'command' if input_type == 'command' else
            'path' if input_type == 'path' else
            'url' if input_type == 'proxy_url' else
            'name' if input_type == 'project_name' else
            'url' if input_type == 'github_url' else
            'text' if input_type == 'text' else
            'key'
        )
    except Exception as e:
        return False, str(e), None


# === Middleware Integration ===

async def validate_and_sanitize(user_input: str, input_type: str = 'text') -> tuple[bool, str, str]:
    """
    Validation and sanitization of user input.

    Args:
        user_input: User input
        input_type: Input type

    Returns:
        (success, error_message, sanitized_value)
    """
    success, error, value = validate_user_input(input_type, user_input)

    if not success:
        logger.warning(f"Input validation failed: {error}")
        return False, error, ""

    return True, "", value


# === Usage Examples ===

# Example 1: Validate command
# success, error, validated = validate_user_input('command', 'ls -la')
# if not success:
#     await message.answer(f"❌ {error}")
#     return

# Example 2: Validate proxy URL
# success, error, validated = validate_user_input('proxy_url', 'http://proxy.example.com:8080')
# if not success:
#     await callback.answer(f"❌ {error}")
#     return

# Example 3: Validate path
# success, error, validated = validate_user_input('path', '/root/projects')
# if not success:
#     await message.answer(f"❌ {error}")
#     return
