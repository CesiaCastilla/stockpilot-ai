"""The shape of tests/evals/scenarios.yaml, validated so a typo fails loudly
instead of quietly testing something else."""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from stockpilot.domain.unit_status import UnitStatus

SCENARIOS_FILE = Path(__file__).with_name("scenarios.yaml")
# Where a unit without a dealer is, in the YAML and in the reports.
CENTER = "CENTER"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Dealer(_Strict):
    city: str
    at: tuple[float, float] = Field(description="Latitude and longitude")
    capacity: int = 30


class Stock(_Strict):
    """Units in the network when the run starts."""

    dealer: str = Field(description=f"A dealer code, or {CENTER}")
    trim: str = Field(description='"<model> <trim>", e.g. "Aconcagua SR 4x4"')
    days: int = Field(ge=0, description="Days since the unit entered the network")
    count: int = Field(default=1, ge=1)
    status: UnitStatus = UnitStatus.IN_YARD
    # For the expectations: a name for these units.
    label: str | None = None
    # Already in a pending transfer order to this dealer.
    pending_to: str | None = None


class Sales(_Strict):
    dealer: str
    trim: str
    count: int = Field(ge=1)
    days_ago: int = Field(default=10, ge=0)


class Request(_Strict):
    max_proposals: int = 5
    trim: str | None = None
    model: str | None = None


class Expect(_Strict):
    """What the run must achieve. Unset means "not judged"."""

    outcome: list[Literal["proposed", "no_proposal", "rejected", "step_limit"]] | None = None
    orders: tuple[int, int] = Field(default=(0, 10), description="Min and max orders")
    destinations: list[str] | None = None
    origins: list[str] | None = None
    chosen: list[str] = Field(default_factory=list, description="Labels: each must be chosen")
    not_chosen: list[str] = Field(default_factory=list, description="Labels: none chosen")
    # After rejecting every order, a different unit must be proposed.
    replacement: bool = False


class Scenario(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
    title: str
    dealers: dict[str, Dealer]
    stock: list[Stock] = Field(default_factory=list)
    sales: list[Sales] = Field(default_factory=list)
    request: Request = Request()
    # People reject every order of the run with this note, and the run resumes.
    reject_with: str | None = None
    expect: Expect

    @model_validator(mode="after")
    def _references_exist(self) -> "Scenario":
        places = {*self.dealers, CENTER}
        labels = {s.label for s in self.stock if s.label}
        for stock in self.stock:
            if stock.dealer not in places or stock.pending_to not in {*self.dealers, None}:
                raise ValueError(f"{self.id}: unknown dealer in {stock}")
        for sale in self.sales:
            if sale.dealer not in self.dealers:
                raise ValueError(f"{self.id}: unknown dealer in {sale}")
        expected = {*(self.expect.destinations or []), *(self.expect.origins or [])}
        if not expected <= places:
            raise ValueError(f"{self.id}: unknown dealer in the expectations")
        if not {*self.expect.chosen, *self.expect.not_chosen} <= labels:
            raise ValueError(f"{self.id}: unknown label in the expectations")
        return self


class ScenarioFile(_Strict):
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def _unique_ids(self) -> "ScenarioFile":
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("Scenario ids must be unique")
        return self


def load_scenarios(path: Path = SCENARIOS_FILE) -> list[Scenario]:
    return ScenarioFile.model_validate(yaml.safe_load(path.read_text())).scenarios
