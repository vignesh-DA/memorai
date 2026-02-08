"""Quick development setup check."""

import sys
import subprocess


def check_python_version():
    """Check Python version."""
    version = sys.version_info
    if version.major == 3 and version.minor >= 11:
        print(f"✓ Python {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        print(f"✗ Python {version.major}.{version.minor} (requires 3.11+)")
        return False


def check_command(cmd, name):
    """Check if a command is available."""
    try:
        subprocess.run(
            [cmd, "--version"],
            capture_output=True,
            check=True,
        )
        print(f"✓ {name} installed")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"✗ {name} not found")
        return False


def check_env_file():
    """Check if .env file exists."""
    import os
    if os.path.exists(".env"):
        print("✓ .env file exists")
        return True
    else:
        print("✗ .env file not found (copy from .env.example)")
        return False


def check_dependencies():
    """Check if dependencies are installed."""
    try:
        import fastapi
        import openai
        import pinecone
        import redis
        import sqlalchemy
        print("✓ Python dependencies installed")
        return True
    except ImportError as e:
        print(f"✗ Missing dependency: {e.name}")
        print("  Run: pip install -r requirements.txt")
        return False


def main():
    """Run all checks."""
    print("\n" + "=" * 60)
    print("Long-Form Memory System - Setup Check")
    print("=" * 60 + "\n")

    checks = [
        ("Python version", check_python_version()),
        ("Docker", check_command("docker", "Docker")),
        ("Docker Compose", check_command("docker-compose", "Docker Compose")),
        ("Environment file", check_env_file()),
        ("Python dependencies", check_dependencies()),
    ]

    print("\n" + "-" * 60)

    all_passed = all(result for _, result in checks)

    if all_passed:
        print("\n✓ All checks passed! You're ready to go.")
        print("\nNext steps:")
        print("  1. Edit .env with your API keys")
        print("  2. Run: docker-compose up -d postgres redis")
        print("  3. Run: python scripts/init_db.py")
        print("  4. Run: uvicorn app.main:app --reload")
    else:
        print("\n✗ Some checks failed. Please fix the issues above.")
        print("\nFor help, see README.md")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
