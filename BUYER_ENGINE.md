# Standalone Buyer Discovery Engine

The buyer_engine directory provides an independent CLI for finding and ranking potential buyers for one explicitly selected product. It does not integrate with or modify Django views, templates, models, or the lead dashboard. Telegram is not implemented.

Run the end-to-end mock pipeline without credentials or network access:

~~~bash
python main.py --product "دستگاه اسپرسوساز" --category "تجهیزات کافه" --city "تهران" --quantity 5 --mode mock
~~~

Use --mode dry-run to inspect a product profile and search queries without collection. Live X and Divar collection require authorized API credentials, and live AI analysis requires an xAI API key:

~~~bash
python main.py --product "espresso machine" --category "coffee equipment" --city "Tehran" --quantity 5 --sources x divar --mode live
~~~

Set X_BEARER_TOKEN, DIVAR_API_KEY, and XAI_API_KEY in the process environment. Configure request, runtime, record, and Grok budgets with the MAX_* and GROK_* environment variables. X follows API next_token pagination up to its request budget. Divar uses bounded query expansion and does not invent pagination. Results are written to output/buyers.json, output/buyers.csv, and output/run_summary.json.

Each invocation creates a persistent SQLite job in JOB_STORE_PATH (default output/jobs.sqlite3) and prints its job ID. List recent jobs with python main.py --list-jobs, inspect one with python main.py --status JOB_ID, request a cooperative pause with python main.py --stop JOB_ID, and continue a paused, interrupted, or budget-limited job with python main.py --resume JOB_ID. Query progress and source records are checkpointed after each completed query. A stop takes effect between requests; an in-flight provider request must return or time out first. If the process exits unexpectedly, resume the job ID printed when it started. Completed jobs cannot be stopped or resumed.

The local Persian classifier contains more than 30 labelled examples. Print its accuracy, precision, recall, F1, confusion matrix and misclassified examples with the --evaluate-persian option. This is an initial hand-built evaluation set, not a guarantee of accuracy on live posts; add reviewed real examples as they become available.

The classifier normalizes Arabic/Persian letter variants, half spaces and Persian/Arabic digits, handles common conversational buying and selling phrases, and extracts numeric quantities including common written Persian numbers. Mixed-intent wording is retained with lower confidence when both explicit buy and sell evidence exists.

Grok calls share the MAX_GROK_REQUESTS job cap across classification and extraction. Returned usage is recorded in the summary. Set XAI_INPUT_USD_PER_MILLION_TOKENS and XAI_OUTPUT_USD_PER_MILLION_TOKENS to current model rates to calculate estimated cost; zero keeps cost reporting disabled until rates are configured. Structured response fields, enums, IDs, confidence, and numeric values are validated; malformed records fall back to conservative local analysis or are skipped.

Vocabulary and generated queries are cached in SQLite at DISCOVERY_CACHE_PATH for VOCABULARY_CACHE_TTL seconds. Cache keys include product, category, attributes, city, quantity, transaction type, language, and query context. Set TTL to 0 to keep entries indefinitely, or clear the cache file to force rediscovery.

Run standalone checks with python -m unittest buyer_engine.tests. Live source schemas and access depend on providers' current official documentation and credentials. Provider errors are included in the job result; the engine does not scrape private endpoints or bypass platform controls.
