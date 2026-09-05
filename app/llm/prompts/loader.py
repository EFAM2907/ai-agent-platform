from dataclasses import dataclass
from pathlib import Path
import re
import yaml


@dataclass
class PromptTemplate:
    name: str
    version: int
    description: str
    system_prompt: str
    variables: list[str]

    def render(self, **kwargs) -> str:
        missing = [v for v in self.variables if v not in kwargs]
        if missing:
            raise ValueError(f"Missing variables for prompt '{self.name}': {missing}")

        rendered = self.system_prompt
        for var_name, value in kwargs.items():
            rendered = rendered.replace(f"{{{var_name}}}", str(value))
        return rendered


class PromptLoader:
    def __init__(self, base_path: str = "app/llm/prompts/templates"):
        self.base_path = Path(base_path)

    def load_latest(self, prompt_name: str) -> PromptTemplate:
        prompt_dir = self.base_path / prompt_name
        if not prompt_dir.exists():
            raise FileNotFoundError(f"No prompt directory found for '{prompt_name}'")

        version_files = list(prompt_dir.glob("v*.yaml"))
        if not version_files:
            raise FileNotFoundError(f"No version files found for prompt '{prompt_name}'")

        def extract_version(path: Path) -> int:
            match = re.match(r"v(\d+)\.yaml", path.name)
            return int(match.group(1)) if match else -1

        latest_file = max(version_files, key=extract_version)
        return self._load_file(prompt_name, latest_file)

    def load_version(self, prompt_name: str, version: int) -> PromptTemplate:
        file_path = self.base_path / prompt_name / f"v{version}.yaml"
        if not file_path.exists():
            raise FileNotFoundError(f"Version {version} not found for prompt '{prompt_name}'")
        return self._load_file(prompt_name, file_path)

    def _load_file(self, prompt_name: str, file_path: Path) -> PromptTemplate:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return PromptTemplate(
            name=prompt_name,
            version=data["version"],
            description=data["description"],
            system_prompt=data["system_prompt"],
            variables=data.get("variables", []),
        )