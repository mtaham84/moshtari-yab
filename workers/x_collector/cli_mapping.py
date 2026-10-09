"""The single source of truth for the Agent Reach twitter CLI mapping."""

import os

CLI_COMMAND = os.getenv("X_CLI_CMD", "twitter")
CLI_SEARCH_SUBCOMMAND = os.getenv("X_CLI_SEARCH_SUBCOMMAND", "search")
CLI_TIME_FILTER = "Latest"
CLI_EXCLUDE_RETWEETS_FLAG = "--exclude"
CLI_EXCLUDE_RETWEETS_VALUE = "retweets"
CLI_LIMIT_FLAG = "--max"
CLI_OUTPUT_FLAG = os.getenv("X_CLI_OUTPUT_FLAG", "--json")

# Candidate output keys are also UNVERIFIED; adapt only this mapping to the
# installed upstream CLI's actual JSON response.
OUTPUT_ITEMS_KEYS = ("data", "tweets", "results", "items")
OUTPUT_ID_KEYS = ("id", "tweet_id", "rest_id")
OUTPUT_TEXT_KEYS = ("text", "full_text", "content")
OUTPUT_HANDLE_KEYS = ("author_handle", "username", "screen_name", "handle", "screenName")
OUTPUT_NAME_KEYS = ("author_name", "name", "display_name")
OUTPUT_CREATED_KEYS = ("createdAtISO", "created_at", "timestamp", "date")
OUTPUT_AUTHOR_ID_KEYS = ("author_id",)
OUTPUT_LANG_KEYS = ("lang",)
OUTPUT_QUERY_KEY = "query"

# Thread collection contract is intentionally isolated and unverified against Agent Reach.
THREAD_STRATEGY = os.getenv("X_THREAD_STRATEGY", "search")
THREAD_SEARCH_QUERY_PREFIX = "conversation_id:"
THREAD_COMMAND = os.getenv("X_CLI_THREAD_SUBCOMMAND", "")
THREAD_OUTPUT_ITEMS_KEYS = OUTPUT_ITEMS_KEYS
