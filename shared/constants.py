"""
Application Constants

Central location for all magic numbers and constants.
Fixes "Magic Numbers" code smell from the code review.
"""

# === HITL Timeouts ===
HITL_PERMISSION_TIMEOUT_SECONDS = 300  # 5 minutes
HITL_QUESTION_TIMEOUT_SECONDS = 300    # 5 minutes
HITL_PLAN_APPROVAL_TIMEOUT_SECONDS = 600  # 10 minutes

# === File Processing ===
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
FILE_CACHE_TTL_SECONDS = 3600  # 1 hour

# === Session Limits ===
MAX_MESSAGES_PER_SESSION = 1000
SESSION_CONTINUITY_HOURS = 24
MAX_CONTEXT_SIZE_BYTES = 100_000

# === Database ===
DB_OLD_COMMANDS_DAYS = 30
DB_OLD_SESSIONS_DAYS = 7

# === Streaming ===
STREAMING_HEARTBEAT_INTERVAL = 5.0  # seconds
STREAMING_MESSAGE_CHAR_LIMIT = 4096
STREAMING_BUFFER_FLUSH_INTERVAL = 0.5  # seconds

# === Claude Code ===
CLAUDE_DEFAULT_MAX_TURNS = 50
CLAUDE_DEFAULT_TIMEOUT_SECONDS = 600
CLAUDE_LINE_READ_TIMEOUT_SECONDS = 60

# === Plugin Descriptions ===
PLUGIN_DESCRIPTIONS = {
    "commit-commands": "Git workflow: commit, push, PR",
    "code-review": "Code review and PR",
    "feature-dev": "Development of features with architecture",
    "frontend-design": "Creation UI interfaces",
    "claude-code-setup": "Settings Claude Code",
    "security-guidance": "Code security check",
    "pr-review-toolkit": "Review tools PR",
    "ralph-loop": "RAFL: iterative problem solving",
}

# === Output Display ===
OUTPUT_HEAD_LIMIT = 1000  # chars to show from start
OUTPUT_TAIL_LIMIT = 500   # chars to show from end
TEXT_TRUNCATE_LIMIT = 3500  # max text for Telegram callback

# === Docker Logs ===
DOCKER_LOGS_PAGE_SIZE = 30  # lines per page
DOCKER_LOGS_MAX_LINES = 200  # max lines to fetch

# === OAuth ===
OAUTH_URL_TIMEOUT_SECONDS = 30

# === Telegram Limits ===
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CALLBACK_DATA_LIMIT = 64

# === Error Messages ===
ERROR_UNAUTHORIZED = "You are not authorized to use this bot."
ERROR_TASK_RUNNING = "The task is already running.\n\nUse the cancel button or /cancel to stop."
ERROR_TIMEOUT = "Timed out."
ERROR_NO_PROJECT = "No active project. Use /change"
ERROR_NO_CONTEXT = "No active context"
