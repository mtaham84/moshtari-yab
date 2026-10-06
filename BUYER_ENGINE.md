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

Run standalone checks with python -m unittest buyer_engine.tests. Live source schemas and access depend on providers' current official documentation and credentials. Provider errors are included in the job result; the engine does not scrape private endpoints or bypass platform controls.
