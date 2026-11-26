"""The eval report, saved in the repository to compare prompt and model versions."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from stockpilot.agents.prompts import SYSTEM_PROMPT
from tests.evals.checks import Check

REPORTS_DIR = Path(__file__).with_name("reports")


@dataclass
class ScenarioResult:
    id: str
    title: str
    checks: list[Check]
    orders: list[str] = field(default_factory=list)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stop_reason: str | None = None
    summary: str | None = None

    @property
    def error(self) -> bool:
        """The run failed (quota, network): no verdict on the model's quality."""
        return any(c.name == "run_finished" and c.passed is False for c in self.checks)

    @property
    def passed(self) -> bool:
        return not self.error and all(c.passed is not False for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.passed is False]


@dataclass
class Report:
    provider: str
    model: str
    started_at: datetime
    selected: int
    total: int
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def prompt_version(self) -> str:
        """Changes when the system prompt changes: reports compare like with like."""
        return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:10]

    def write(self, directory: Path = REPORTS_DIR) -> Path:
        directory.mkdir(exist_ok=True)
        stem = f"{self.started_at:%Y-%m-%d_%H%M}_{self.model.replace('/', '-')}"
        (directory / f"{stem}.json").write_text(
            json.dumps(self._data(), ensure_ascii=False, indent=2) + "\n"
        )
        markdown = directory / f"{stem}.md"
        markdown.write_text(self._markdown())
        return markdown

    def _data(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "started_at": self.started_at.isoformat(),
            "scenarios": f"{self.selected} of {self.total}",
            "passed": sum(r.passed for r in self.results),
            "errors": sum(r.error for r in self.results),
            "input_tokens": sum(r.input_tokens for r in self.results),
            "output_tokens": sum(r.output_tokens for r in self.results),
            "results": [{**asdict(r), "passed": r.passed, "error": r.error} for r in self.results],
        }

    def _markdown(self) -> str:
        passed = sum(r.passed for r in self.results)
        errors = sum(r.error for r in self.results)
        judged = len(self.results) - errors
        lines = [
            f"# Agent evals: {self.provider} / {self.model}",
            "",
            f"- Run: {self.started_at:%Y-%m-%d %H:%M} UTC",
            f"- Prompt version: `{self.prompt_version}`",
            f"- Scenarios: {self.selected} of {self.total}",
            f"- **Passed: {passed} of {judged}**"
            + (f" ({errors} could not run)" if errors else ""),
            f"- Tokens: {sum(r.input_tokens for r in self.results):,} in, "
            f"{sum(r.output_tokens for r in self.results):,} out",
            "",
            "| Scenario | Result | Orders | Model calls | Failed checks |",
            "|---|---|---|---|---|",
        ]
        for r in self.results:
            result = "error" if r.error else ("pass" if r.passed else "**fail**")
            failed = "; ".join(f"{c.name}: {c.detail}" for c in r.failures) or "-"
            lines.append(f"| `{r.id}` | {result} | {len(r.orders)} | {r.model_calls} | {failed} |")
        lines += ["", "## Checks by scenario", ""]
        for r in self.results:
            lines.append(f"### `{r.id}`: {r.title}")
            lines.append("")
            for c in r.checks:
                mark = {True: "pass", False: "FAIL", None: "n/a"}[c.passed]
                lines.append(f"- {mark} `{c.name}`" + (f": {c.detail}" if c.detail else ""))
            for order in r.orders:
                lines.append(f"- order: {order}")
            if r.summary:
                lines.append(f"- model's answer: {r.summary}")
            lines.append("")
        return "\n".join(lines)
