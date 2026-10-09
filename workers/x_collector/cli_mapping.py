"""The only place containing Agent Reach / twitter-cli specific assumptions.

Upstream CLI availability, command syntax, flags, and JSON shape were not
verifiable in this environment. Treat these defaults as UNVERIFIED and
override them after checking the installed CLI's own help output.
"""

import os

CLI_COMMAND = os.getenv("X_CLI_CMD", "twitter-cli")
CLI_SEARCH_SUBCOMMAND = os.getenv("X_CLI_SEARCH_SUBCOMMAND", "search")
CLI_QUERY_FLAG = os.getenv("X_CLI_QUERY_FLAG", "--query")
CLI_LIMIT_FLAG = os.getenv("X_CLI_LIMIT_FLAG", "--limit")
CLI_OUTPUT_FLAG = os.getenv("X_CLI_OUTPUT_FLAG", "--json")

# Candidate output keys are also UNVERIFIED; adapt only this mapping to the
# installed upstream CLI's actual JSON response.
OUTPUT_ITEMS_KEYS = ("tweets", "results", "data", "items")
OUTPUT_ID_KEYS = ("id", "tweet_id", "rest_id")
OUTPUT_TEXT_KEYS = ("text", "full_text", "content")
OUTPUT_HANDLE_KEYS = ("author_handle", "username", "screen_name", "handle", "screenName")
OUTPUT_NAME_KEYS = ("author_name", "name", "display_name")
OUTPUT_CREATED_KEYS = ("created_at", "timestamp", "date")
OUTPUT_AUTHOR_ID_KEYS = ("author_id",)
OUTPUT_LANG_KEYS = ("lang",)
OUTPUT_QUERY_KEY = "query"
