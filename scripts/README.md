# Scripts Directory

Utility scripts for development and deployment.

## Available Scripts

### `init_db.py`
Initialize database schema with tables and indexes.

```bash
python scripts/init_db.py
```

Creates:
- `memories` table with pgvector support
- `conversation_turns` table
- Necessary indexes for performance

### `setup_check.py`
Verify development environment setup.

```bash
python scripts/setup_check.py
```

Checks:
- Python version (3.11+)
- Docker installation
- Required dependencies
- Environment configuration

## Usage

Always run scripts from project root:
```bash
cd "e:\Long Term Memory"
python scripts/<script_name>.py
```
