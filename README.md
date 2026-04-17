# Political Email Archive

An archive of political fundraising emails, with daily updates via IMAP.

Data is stored as daily JSONL files organized by date: `data/YYYY/MM/YYYY-MM-DD.jsonl`

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- Gmail account with [App Password](https://support.google.com/accounts/answer/185833) enabled

### Install

```bash
uv sync
```

### Environment Variables

```bash
export GMAIL_USER="your-email@gmail.com"
export GMAIL_APP_PASSWORD="your-app-password"
```

## Scripts

### Initial Migration

Convert an existing MBOX file into daily JSONL files:

```bash
cd scripts
uv run python migrate_mbox.py --mbox-path /path/to/your.mbox
```

### Daily Collection

Fetch new emails via IMAP since the last watermark:

```bash
cd scripts
uv run python collect_emails.py
```

Options:
- `--since YYYY-MM-DD` — override the start date
- `--dry-run` — count messages without processing
- `--folder "INBOX"` — specify IMAP folder (default: `"[Gmail]/All Mail"`)

### Build Site

Generate the static index page and download archives:

```bash
cd scripts
uv run python build_site.py
```

## Data Format

Each line in a JSONL file is a JSON record with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | string | Email Message-ID header |
| `name` | string | Sender display name |
| `email` | string | Sender email address |
| `subject` | string | Email subject line |
| `domain` | string | Sender domain |
| `party` | string/null | Political party (D, R, or null) |
| `disclaimer` | boolean | Has "Paid for by" disclaimer |
| `disclaimer_text` | string | Full disclaimer text |
| `date` | string | ISO 8601 datetime |
| `year` | integer | Year |
| `month` | integer | Month (1-12) |
| `day` | integer | Day of month |
| `hour` | integer | Hour (0-23) |
| `minute` | integer | Minute (0-59) |
| `body` | string | Lightly cleaned email body |
| `clean_body` | string | Aggressively cleaned body (no HTML, no boilerplate) |
| `urls` | array | URLs found in the email body |

## Automation

GitHub Actions runs daily:
1. **Collect** (10am UTC): Fetches new emails via IMAP, commits to `data/`
2. **Deploy** (11:30am UTC): Builds the static site and deploys to GitHub Pages

## License

MIT
