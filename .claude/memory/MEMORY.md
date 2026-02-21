# Project Memory: Claude Code Telegram Bot

## Project Overview
- **Name**: Claude Code Telegram Bot
- **Architecture**: DDD (Domain-Driven Design) with 4 layers: Domain → Application → Infrastructure → Presentation
- **Purpose**: Telegram interface for Claude Code CLI/SDK enabling AI coding via Telegram

## Key Configuration
- **Telegram Token**: `8272834607:AAFcqo1GmTl8QxoY4e5msiN5Viu3KJPXa9M`
- **Allowed User ID**: `260923111`
- **Bot Username**: @noonoonClaudebot

## Development Environment
- Uses **rtk** (Rust Token Killer) for command optimization
- Python 3.11 in Docker container
- Docker Compose for deployment
- SQLite for data persistence

## Important Fixes Applied

### 1. OAuth Support Without API Key
- Modified `shared/config/settings.py` to make API key optional
- Modified `domain/value_objects/ai_provider_config.py` to allow empty API key
- This enables Claude Account OAuth mode without requiring API key at startup

### 2. Auth Middleware Auto-Create Users
- Modified `presentation/middleware/auth.py` to auto-create users when whitelist check passes
- Users are automatically created on first message if their ID is in ALLOWED_USER_ID

### 3. Command Handler Routing Fix
- Fixed `presentation/handlers/message/coordinator.py` to exclude commands from text handler
- Changed: `F.text` → `F.text & ~F.text.startswith("/")`
- This ensures `/account`, `/login`, `/start` etc. are handled by Command handlers, not sent to Claude

## Session Storage Locations
1. **Project Contexts**: `./data/bot.db` (SQLite) - conversation history per project
2. **Claude Native Sessions**: `./claude_sessions/` (mounted to `/root/.claude`)
3. **OAuth Credentials**: Stored in `./claude_sessions/.credentials.json`

## Known Limitations
- **OAuth does NOT auto-renew** - When token expires, user must re-login via `/account`
- No automatic token refresh using refreshToken (stored but not used)

## Docker Volumes
```yaml
- ./data:/app/data                    # SQLite database
- ./claude_sessions:/root/.claude     # Claude Code sessions & OAuth
- ./projects:/root/projects           # User project files
- ./claude_config:/root/.config/claude:ro  # OAuth credentials mount
```

## Useful Commands
```bash
# Run locally
python main.py

# Docker
docker-compose up -d --build
docker-compose logs -f claude-bot

# View last 100 lines via HTTP API
curl "http://192.168.0.116:9999/logs/claude_agent?tail=100"
```

## Files Modified in This Session
1. `.env` - Configuration
2. `shared/config/settings.py` - Made API key optional
3. `domain/value_objects/ai_provider_config.py` - Allow empty API key
4. `presentation/middleware/auth.py` - Auto-create users
5. `presentation/handlers/message/coordinator.py` - Fix command routing
6. `docker-compose.yml` - Added claude_config volume mount
