"""Prompts package for mcp-windbg."""

from pathlib import Path


def get_prompts_directory() -> Path:
    """Get the path to the prompts directory."""
    return Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt file by name.

    Args:
        name: The prompt name (without .prompt.md extension)

    Returns:
        The content of the prompt file

    Raises:
        FileNotFoundError: If the prompt file doesn't exist
    """
    prompts_dir = get_prompts_directory()
    prompt_path = prompts_dir / f"{name}.prompt.md"

    if not prompt_path.exists():  # pragma: no cover - the packaged prompt file is always present
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")
